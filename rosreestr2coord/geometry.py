"""
Small dependency-free geometry helpers for GeoJSON geometries in WGS84 (lon/lat).

They are only meant to support the map output (extent, label placement, area check),
not to replace a real geometry library.
"""

import heapq
import math
from typing import Iterator, List, Optional, Sequence, Tuple

from .utils import geometry_to_wgs

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)
WGS84_E = math.sqrt(WGS84_E2)

POLYGON_TYPES = ("Polygon", "MultiPolygon")
POINT_TYPES = ("Point", "MultiPoint")
LINE_TYPES = ("LineString", "MultiLineString")

Position = Sequence[float]
Ring = Sequence[Position]
Polygon = Sequence[Ring]


def geometry_type(geometry: Optional[dict]) -> str:
    return (geometry or {}).get("type") or ""


def is_polygonal(geometry: Optional[dict]) -> bool:
    """True if the geometry contains at least one polygon with a usable outer ring."""
    return any(True for _ in iter_polygons(geometry))


def geometry_kind(geometry: Optional[dict]) -> str:
    """
    What can be drawn for the geometry:
    "polygon" - an outline of an area, "line" - a linear object (roads, pipelines, power lines ...),
    "point" - only a location, "" - nothing.
    """
    if is_polygonal(geometry):
        return "polygon"
    if any(True for _ in iter_lines(geometry)):
        return "line"
    if any(True for _ in iter_positions(geometry)):
        return "point"
    return ""


def has_shape(geometry: Optional[dict]) -> bool:
    """True if the geometry shows where the object lies on the ground (an outline or a line), not just a point."""
    return geometry_kind(geometry) in ("polygon", "line")


def iter_lines(geometry: Optional[dict]) -> Iterator[Ring]:
    """Yield every line (list of positions) of a (Multi)LineString geometry."""
    gtype = geometry_type(geometry)
    coords = (geometry or {}).get("coordinates") or []
    if gtype == "LineString":
        candidates = [coords]
    elif gtype == "MultiLineString":
        candidates = coords
    elif gtype == "GeometryCollection":
        for sub in (geometry or {}).get("geometries") or []:
            for line in iter_lines(sub):
                yield line
        return
    else:
        return
    for line in candidates:
        if line and len(line) >= 2:
            yield line


def iter_polygons(geometry: Optional[dict]) -> Iterator[Polygon]:
    """Yield every polygon (list of rings, the first one is the outer ring) of a geometry."""
    gtype = geometry_type(geometry)
    coords = (geometry or {}).get("coordinates") or []
    if gtype == "Polygon":
        candidates = [coords]
    elif gtype == "MultiPolygon":
        candidates = coords
    elif gtype == "GeometryCollection":
        for sub in (geometry or {}).get("geometries") or []:
            for polygon in iter_polygons(sub):
                yield polygon
        return
    else:
        return
    for polygon in candidates:
        if polygon and len(polygon[0]) >= 3:
            yield polygon


def iter_positions(geometry: Optional[dict]) -> Iterator[Position]:
    """Yield every [lon, lat] position of a geometry of any type."""
    if not geometry:
        return
    if geometry_type(geometry) == "GeometryCollection":
        for sub in geometry.get("geometries") or []:
            for position in iter_positions(sub):
                yield position
        return
    stack = [geometry.get("coordinates")]
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)) and item:
            if isinstance(item[0], (int, float)):
                if len(item) >= 2:
                    yield item
            else:
                stack.extend(reversed(item))


def geometry_bbox(geometry: Optional[dict]) -> Optional[Tuple[float, float, float, float]]:
    """(min_lon, min_lat, max_lon, max_lat) or None for an empty geometry."""
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf
    for position in iter_positions(geometry):
        lon, lat = position[0], position[1]
        min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
        min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
    if min_lon == math.inf:
        return None
    return min_lon, min_lat, max_lon, max_lat


def merge_bboxes(bboxes: Sequence[Optional[Tuple[float, float, float, float]]]):
    bboxes = [b for b in bboxes if b]
    if not bboxes:
        return None
    return (
        min(b[0] for b in bboxes),
        min(b[1] for b in bboxes),
        max(b[2] for b in bboxes),
        max(b[3] for b in bboxes),
    )


# --- area ---------------------------------------------------------------------------------------------------------


def _authalic_q(sin_phi: float) -> float:
    es = WGS84_E * sin_phi
    return (1 - WGS84_E2) * (sin_phi / (1 - es * es) - (1 / (2 * WGS84_E)) * math.log((1 - es) / (1 + es)))


def equal_area_xy(lon: float, lat: float) -> Tuple[float, float]:
    """
    Cylindrical equal-area projection on the WGS84 ellipsoid (metres).

    Planar areas measured in this projection are true areas on the ellipsoid, which is what is needed to
    compare a drawn outline with the area recorded in the registry. (Web Mercator would overstate the area
    by a factor of 1 / cos(lat)^2 - about three times at the latitude of Moscow.)
    """
    lat = max(-90.0, min(90.0, lat))
    return WGS84_A * math.radians(lon), WGS84_A * _authalic_q(math.sin(math.radians(lat))) / 2


def _shoelace(points: Sequence[Tuple[float, float]]) -> float:
    """Signed planar area; coordinates are shifted to the first vertex to keep precision."""
    if len(points) < 3:
        return 0.0
    x0, y0 = points[0]
    total = 0.0
    px, py = 0.0, 0.0
    for x, y in points[1:]:
        x, y = x - x0, y - y0
        total += px * y - x * py
        px, py = x, y
    # closing segment back to the first vertex contributes nothing (it is the origin)
    return total / 2


def ring_area_m2(ring: Ring) -> float:
    """Unsigned area of a closed ring given in lon/lat degrees, in square metres."""
    return abs(_shoelace([equal_area_xy(p[0], p[1]) for p in ring]))


def polygon_area_m2(polygon: Polygon) -> float:
    if not polygon:
        return 0.0
    area = ring_area_m2(polygon[0]) - sum(ring_area_m2(hole) for hole in polygon[1:])
    return max(area, 0.0)


def geometry_area_m2(geometry: Optional[dict]) -> float:
    """Area of a (Multi)Polygon in square metres on the WGS84 ellipsoid; 0 for other geometry types."""
    return sum(polygon_area_m2(polygon) for polygon in iter_polygons(geometry))


# --- label placement ----------------------------------------------------------------------------------------------


def _local_projector(lon0: float, lat0: float):
    """Plain local metric projection around a point - good enough for placing labels."""
    k = math.cos(math.radians(lat0))
    scale = math.pi / 180 * 6371008.8

    def forward(lon: float, lat: float) -> Tuple[float, float]:
        return (lon - lon0) * k * scale, (lat - lat0) * scale

    def inverse(x: float, y: float) -> List[float]:
        return [lon0 + x / (k * scale) if k else lon0, lat0 + y / scale]

    return forward, inverse


def _seg_dist_sq(px: float, py: float, a: Tuple[float, float], b: Tuple[float, float]) -> float:
    x, y = a
    dx, dy = b[0] - x, b[1] - y
    if dx or dy:
        t = ((px - x) * dx + (py - y) * dy) / (dx * dx + dy * dy)
        if t > 1:
            x, y = b
        elif t > 0:
            x += dx * t
            y += dy * t
    dx, dy = px - x, py - y
    return dx * dx + dy * dy


def _signed_distance(px: float, py: float, polygon: Sequence[Sequence[Tuple[float, float]]]) -> float:
    """Distance from a point to the polygon outline: positive inside, negative outside."""
    inside = False
    min_dist_sq = math.inf
    for ring in polygon:
        j = len(ring) - 1
        for i in range(len(ring)):
            a, b = ring[i], ring[j]
            if (a[1] > py) != (b[1] > py) and px < (b[0] - a[0]) * (py - a[1]) / (b[1] - a[1]) + a[0]:
                inside = not inside
            dist_sq = _seg_dist_sq(px, py, a, b)
            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
            j = i
    if min_dist_sq == math.inf:
        return -math.inf
    return (1 if inside else -1) * math.sqrt(min_dist_sq)


def _ring_centroid(ring: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    area = cx = cy = 0.0
    j = len(ring) - 1
    for i in range(len(ring)):
        a, b = ring[i], ring[j]
        f = a[0] * b[1] - b[0] * a[1]
        cx += (a[0] + b[0]) * f
        cy += (a[1] + b[1]) * f
        area += f * 3
        j = i
    if not area:
        return None
    return cx / area, cy / area


def _decimate(ring: Sequence[Tuple[float, float]], max_points: int) -> List[Tuple[float, float]]:
    if len(ring) <= max_points:
        return list(ring)
    step = len(ring) / float(max_points)
    return [ring[int(i * step)] for i in range(max_points)]


def _polylabel(polygon: Sequence[Sequence[Tuple[float, float]]], precision: float, max_cells: int = 4000):
    """
    Pole of inaccessibility - the point inside a polygon that is farthest from its outline
    (the algorithm of mapbox/polylabel). Returns (x, y, distance).
    """
    outer = polygon[0]
    min_x = min(p[0] for p in outer)
    max_x = max(p[0] for p in outer)
    min_y = min(p[1] for p in outer)
    max_y = max(p[1] for p in outer)
    width, height = max_x - min_x, max_y - min_y
    cell_size = min(width, height)
    if cell_size <= 0:
        return min_x, min_y, 0.0

    counter = [0]

    def make_cell(x: float, y: float, h: float):
        d = _signed_distance(x, y, polygon)
        counter[0] += 1
        # heapq is a min-heap: order by the best distance a cell could still contain, largest first
        return (-(d + h * math.sqrt(2)), counter[0], x, y, h, d)

    queue: list = []
    h = cell_size / 2
    x = min_x
    while x < max_x:
        y = min_y
        while y < max_y:
            heapq.heappush(queue, make_cell(x + h, y + h, h))
            y += cell_size
        x += cell_size

    best = make_cell(min_x + width / 2, min_y + height / 2, 0)
    centroid = _ring_centroid(outer)
    if centroid:
        candidate = make_cell(centroid[0], centroid[1], 0)
        if candidate[5] > best[5]:
            best = candidate

    while queue and counter[0] < max_cells:
        cell = heapq.heappop(queue)
        _, _, cx, cy, ch, cd = cell
        if cd > best[5]:
            best = cell
        if -cell[0] - best[5] <= precision:
            continue
        ch /= 2
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            heapq.heappush(queue, make_cell(cx + dx * ch, cy + dy * ch, ch))
    return best[2], best[3], best[5]


def polygon_label_point(polygon: Polygon) -> Optional[List[float]]:
    """A point that is safely inside the polygon (lon, lat) - the place to put its label."""
    if not polygon or len(polygon[0]) < 3:
        return None
    outer = polygon[0]
    lon0 = sum(p[0] for p in outer) / len(outer)
    lat0 = sum(p[1] for p in outer) / len(outer)
    forward, inverse = _local_projector(lon0, lat0)
    # Label placement does not need every vertex; keep the search fast for very detailed outlines.
    budget = max(40, 600 // max(1, len(polygon)))
    projected = [_decimate([forward(p[0], p[1]) for p in ring], budget) for ring in polygon if len(ring) >= 3]
    xs = [p[0] for p in projected[0]]
    ys = [p[1] for p in projected[0]]
    size = max(max(xs) - min(xs), max(ys) - min(ys))
    if size <= 0:
        return [outer[0][0], outer[0][1]]
    x, y, distance = _polylabel(projected, precision=size / 200.0)
    if distance <= 0:
        # degenerate outline (self-intersections, zero area): fall back to the middle of the extent
        x, y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return inverse(x, y)


def distance_m(a: Position, b: Position) -> float:
    """Distance between two close [lon, lat] positions in metres (haversine)."""
    lon1, lat1, lon2, lat2 = (math.radians(v) for v in (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def line_length_m(line: Ring) -> float:
    return sum(distance_m(line[i], line[i + 1]) for i in range(len(line) - 1))


def geometry_length_m(geometry: Optional[dict]) -> float:
    """Total length of a (Multi)LineString in metres; 0 for other geometry types."""
    return sum(line_length_m(line) for line in iter_lines(geometry))


def line_midpoint(line: Ring) -> Optional[List[float]]:
    """The point in the middle of a line, measured along the line."""
    if not line:
        return None
    half = line_length_m(line) / 2
    walked = 0.0
    for i in range(len(line) - 1):
        step = distance_m(line[i], line[i + 1])
        if step > 0 and walked + step >= half:
            t = (half - walked) / step
            return [line[i][0] + (line[i + 1][0] - line[i][0]) * t, line[i][1] + (line[i + 1][1] - line[i][1]) * t]
        walked += step
    return [line[0][0], line[0][1]]


def largest_polygon(geometry: Optional[dict]) -> Optional[Polygon]:
    best, best_area = None, -1.0
    for polygon in iter_polygons(geometry):
        area = polygon_area_m2(polygon)
        if area > best_area:
            best, best_area = polygon, area
    return best


def geometry_center(geometry: Optional[dict]) -> Optional[List[float]]:
    """
    A representative [lon, lat] for a geometry: an interior point of the largest part for polygons,
    the middle of the longest line for lines, the point itself for points, the middle of the extent otherwise.
    """
    geometry = geometry_to_wgs(geometry) if geometry else geometry
    polygon = largest_polygon(geometry)
    if polygon is not None:
        point = polygon_label_point(polygon)
        if point:
            return point
    lines = sorted(iter_lines(geometry), key=line_length_m, reverse=True)
    if lines:
        point = line_midpoint(lines[0])
        if point:
            return point
    if geometry_type(geometry) == "Point":
        coords = (geometry or {}).get("coordinates") or []
        if len(coords) >= 2:
            return [coords[0], coords[1]]
    bbox = geometry_bbox(geometry)
    if bbox:
        return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
    return None


def bbox_size_m(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Approximate width and height of a lon/lat extent in metres."""
    forward, _ = _local_projector((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    x1, y1 = forward(bbox[0], bbox[1])
    x2, y2 = forward(bbox[2], bbox[3])
    return abs(x2 - x1), abs(y2 - y1)
