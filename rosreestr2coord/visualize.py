"""
Maps of the objects received from NSPD.

* `write_html` - one self-contained HTML file with an interactive map (Leaflet is embedded, only the
  background tiles are loaded from the internet when the page is opened);
* `write_png` - a static picture (needs matplotlib).

Both take a list of results - one per requested number, see `batch.make_result` - and show every requested
number: the ones without boundaries are listed with the reason.
"""

import datetime
import html
import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .attributes import attribute_rows, feature_number, summarize
from .geometry import (
    geometry_area_m2,
    geometry_bbox,
    geometry_center,
    geometry_kind,
    geometry_length_m,
    iter_lines,
    iter_polygons,
    merge_bboxes,
    polygon_area_m2,
    polygon_label_point,
)
from .utils import clean_code, codes_equal, is_cadastral_number, transform_to_wgs

STATUS_OK = "ok"
STATUS_NO_BOUNDS = "no_bounds"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"
_STATUSES = (STATUS_OK, STATUS_NO_BOUNDS, STATUS_NOT_FOUND, STATUS_ERROR, STATUS_SKIPPED)

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(_HERE, "templates", "map.html")
_LEAFLET = os.path.join(_HERE, "vendor", "leaflet")

# Background maps of the interactive map, in the order of preference: if the tiles of one can not be loaded,
# the page switches to the next one. `maxNativeZoom` - the deepest zoom the provider has tiles for
# (deeper zooms are shown by stretching them).
DEFAULT_BASEMAPS: List[Dict[str, Any]] = [
    {
        "name": "Satellite (Esri)",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        "maxNativeZoom": 17,
    },
    {
        "name": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        "maxNativeZoom": 19,
    },
    {
        "name": "OpenTopoMap",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": (
            'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM; '
            'style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)'
        ),
        "maxNativeZoom": 17,
    },
]

# Colours of the groups (cadastral quarters): bright enough to be seen over satellite imagery;
# on the map every outline also gets a dark casing, so they stay visible over a light background.
PALETTE = [
    "#ffd60a",
    "#00d4ff",
    "#ff5ea8",
    "#7dff5a",
    "#ff8a1f",
    "#b794ff",
    "#ff5a4f",
    "#3fe0c5",
    "#ffffff",
    "#c0f000",
]

_COORD_DIGITS = 7  # ~1 cm
# An object of many contours (for example a plot under the pylons of a power line) gets a label on the largest ones
_MAX_LABELS = 40


class MapDependencyError(RuntimeError):
    """An optional package needed for the requested kind of map is not installed."""


# ----------------------------------------------------------------------------------------------------------------
# input


def _features_of(document: Any) -> List[dict]:
    """
    Features of any JSON document that may hold them: a Feature, a FeatureCollection, a list of them,
    or a raw answer of the NSPD search (`{"data": {"type": "FeatureCollection", ...}}`).
    """
    if isinstance(document, list):
        features: List[dict] = []
        for part in document:
            features.extend(_features_of(part))
        return features
    if not isinstance(document, dict):
        return []
    if document.get("type") == "Feature":
        return [document]
    if isinstance(document.get("features"), list):
        return _features_of(document["features"])
    if isinstance(document.get("data"), (dict, list)):
        return _features_of(document["data"])
    return []


def load_features(paths: Iterable[str]) -> List[dict]:
    """
    Read features from GeoJSON / JSON files and directories with them (not recursive).
    Coordinates are converted to WGS84 if they are in Web Mercator, as NSPD returns them.
    An object that occurs several times (a file per object and a file with all of them) is taken once.
    """
    files: List[str] = []
    for path in paths:
        if os.path.isdir(path):
            names = sorted(n for n in os.listdir(path) if n.lower().endswith((".geojson", ".json")))
            files.extend(os.path.join(path, n) for n in names)
        else:
            files.append(path)

    features: List[dict] = []
    seen = set()
    for path in files:
        with open(path, "r", encoding="utf-8-sig") as f:
            try:
                document = json.load(f)
            except ValueError as er:
                raise ValueError("%s is not a JSON file: %s" % (path, er)) from er
        mtime = os.path.getmtime(path)
        for feature in _features_of(document):
            if not isinstance(feature.get("geometry"), dict) and not feature.get("properties"):
                continue
            feature = transform_to_wgs(feature)
            number = feature_number(feature)
            key = number or json.dumps(feature.get("geometry"), sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            feature["_r2c_file_time"] = mtime
            features.append(feature)
    return features


def _status_of(feature: Optional[dict]) -> str:
    if not feature:
        return STATUS_NOT_FOUND
    return STATUS_OK if geometry_kind(feature.get("geometry")) in ("polygon", "line") else STATUS_NO_BOUNDS


def results_from_features(features: Sequence[dict], codes: Optional[Sequence[str]] = None) -> List[dict]:
    """
    Results (as `batch.make_result` makes them) for features read from files.
    With `codes` the result follows that list: one entry per code, the ones that are not in the files are marked.
    """
    features = list(features)

    def result(code: str, feature: Optional[dict], status: str, message: str = "") -> dict:
        retrieved = None
        if feature is not None:
            feature = dict(feature)
            retrieved = feature.pop("_r2c_file_time", None)
        return {
            "code": code,
            "status": status,
            "feature": feature,
            "message": message,
            "cached": True,
            "retrieved": retrieved,
        }

    if codes is None:
        results = []
        for index, feature in enumerate(features):
            code = feature_number(feature) or "object %i" % (index + 1)
            results.append(result(code, feature, _status_of(feature)))
        return results

    results = []
    for code in codes:
        code = clean_code(code)
        match = next((f for f in features if codes_equal(code, feature_number(f))), None)
        if match is None:
            results.append(result(code, None, STATUS_SKIPPED, "there is no data for this number in the given files"))
        else:
            results.append(result(code, match, _status_of(match)))
    return results


# ----------------------------------------------------------------------------------------------------------------
# what to draw


def _round_coords(coords: Any) -> Any:
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)):
            return [round(coords[0], _COORD_DIGITS), round(coords[1], _COORD_DIGITS)]
        return [_round_coords(c) for c in coords]
    return coords


def _drawable_geometry(geometry: Optional[dict]) -> Optional[dict]:
    """A plain (Multi)Polygon, (Multi)LineString or Point with rounded coordinates - or None."""
    kind = geometry_kind(geometry)
    if kind == "polygon":
        polygons = [[ring for ring in polygon if len(ring) >= 3] for polygon in iter_polygons(geometry)]
        return {"type": "MultiPolygon", "coordinates": _round_coords(polygons)}
    if kind == "line":
        return {"type": "MultiLineString", "coordinates": _round_coords(list(iter_lines(geometry)))}
    if kind == "point":
        center = geometry_center(geometry)
        return {"type": "Point", "coordinates": _round_coords(center)} if center else None
    return None


def _label_points(geometry: Optional[dict], center: Optional[Sequence[float]]) -> List[List[float]]:
    """
    Where to write the number of the object, as [lat, lon]: the first point is the main one,
    then one for every other contour, from the largest contour to the smallest.
    """
    if not center:
        return []
    points = [[round(center[1], _COORD_DIGITS), round(center[0], _COORD_DIGITS)]]
    polygons = sorted(iter_polygons(geometry), key=polygon_area_m2, reverse=True)
    for polygon in polygons[1:_MAX_LABELS]:
        point = polygon_label_point(polygon)
        if point:
            points.append([round(point[1], _COORD_DIGITS), round(point[0], _COORD_DIGITS)])
    return points


def _group_of(code: str, info: dict) -> str:
    """Objects are coloured by cadastral quarter."""
    if info.get("quarter"):
        return str(info["quarter"])
    if is_cadastral_number(code):
        return code.rsplit(":", 1)[0]
    return ""


def _short_label(code: str) -> str:
    """`38:06:144003:4723` -> `:11` (the quarter is shown by the colour)."""
    if is_cadastral_number(code):
        return ":" + code.rsplit(":", 1)[1]
    return code


def build_items(results: Sequence[dict]) -> List[dict]:
    """Everything the maps need to know about each requested number."""
    items: List[dict] = []
    groups: List[str] = []
    for index, result in enumerate(results):
        feature = result.get("feature")
        code = result.get("code") or feature_number(feature) or "object %i" % (index + 1)
        status = result.get("status") or _status_of(feature)
        info = summarize(feature)
        geometry = _drawable_geometry((feature or {}).get("geometry")) if feature else None
        kind = geometry_kind(geometry)
        if status == STATUS_OK and kind not in ("polygon", "line"):
            # never draw an outline that is not there
            status = STATUS_NO_BOUNDS
        center = geometry_center(geometry) if geometry else None
        group = _group_of(code, info)
        if group not in groups:
            groups.append(group)
        items.append(
            {
                "n": index + 1,
                "code": code,
                "status": status,
                "message": result.get("message") or "",
                "cached": bool(result.get("cached")),
                "retrieved": result.get("retrieved"),
                "group": group,
                "color": PALETTE[groups.index(group) % len(PALETTE)],
                "short": _short_label(code),
                "info": info,
                "rows": [list(row) for row in attribute_rows(feature)] if feature else [],
                "kind": kind,
                "geometry": geometry,
                "center": [round(center[1], _COORD_DIGITS), round(center[0], _COORD_DIGITS)] if center else None,
                "labels": _label_points(geometry, center),
                "bbox": geometry_bbox(geometry),
                "area_outline": round(geometry_area_m2(geometry), 1) if kind == "polygon" else None,
                "length": round(geometry_length_m(geometry), 1) if kind == "line" else None,
                "parts": len(geometry["coordinates"]) if kind in ("polygon", "line") else 0,
            }
        )
    return items


def _counts(items: Sequence[dict]) -> Dict[str, int]:
    return {status: sum(1 for item in items if item["status"] == status) for status in _STATUSES}


def _groups(items: Sequence[dict]) -> List[dict]:
    groups: List[dict] = []
    for item in items:
        if not item["geometry"] or not item["group"]:
            continue
        found = next((g for g in groups if g["id"] == item["group"]), None)
        if found:
            found["count"] += 1
        else:
            groups.append({"id": item["group"], "color": item["color"], "count": 1})
    return groups


def _subtitle(generated: float) -> str:
    stamp = datetime.datetime.fromtimestamp(generated, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "Source: NSPD geoportal (nspd.gov.ru). Map made on %s" % stamp


# ----------------------------------------------------------------------------------------------------------------
# interactive map


def _read(path: str, mode: str = "r"):
    if "b" in mode:
        with open(path, mode) as f:
            return f.read()
    with open(path, mode, encoding="utf-8") as f:
        return f.read()


def _data_uri(path: str, mime: str) -> str:
    import base64

    return "data:%s;base64,%s" % (mime, base64.b64encode(_read(path, "rb")).decode("ascii"))


def _leaflet_css() -> str:
    css = _read(os.path.join(_LEAFLET, "leaflet.css"))
    layers = _data_uri(os.path.join(_LEAFLET, "layers.png"), "image/png")
    css = css.replace("url(images/layers.png)", "url(%s)" % layers)
    css = css.replace(
        "url(images/layers-2x.png)", "url(%s)" % _data_uri(os.path.join(_LEAFLET, "layers-2x.png"), "image/png")
    )
    # the default marker image is not used (and is not shipped)
    css = css.replace("url(images/marker-icon.png)", "none")
    return css


def _leaflet_js() -> str:
    js = _read(os.path.join(_LEAFLET, "leaflet.js"))
    js = re.sub(r"//# sourceMappingURL=\S+\s*$", "", js)
    # a script element ends at the first "</script" whatever the context is
    return js.replace("</script", "<\\/script")


def _json_for_html(data: Any) -> str:
    """JSON that is safe inside a <script> element."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def build_html(
    results: Sequence[dict],
    title: str = "Cadastral objects",
    note: Optional[str] = None,
    basemaps: Optional[Sequence[dict]] = None,
    generated: Optional[float] = None,
) -> str:
    """
    The interactive map as the text of one self-contained HTML page.

    `results` - a result per requested number (`batch.make_result`), `note` - a remark shown above the list,
    `basemaps` - background tile layers instead of `DEFAULT_BASEMAPS` (an empty list - no background).
    """
    generated = time.time() if generated is None else generated
    items = build_items(results)
    for item in items:
        item.pop("bbox", None)
    data = {
        "title": title,
        "subtitle": _subtitle(generated),
        "note": note or "",
        "counts": _counts(items),
        "groups": _groups(items),
        "basemaps": list(DEFAULT_BASEMAPS if basemaps is None else basemaps),
        "items": items,
    }
    parts = {
        "TITLE": html.escape(title),
        "LEAFLET_CSS": _leaflet_css(),
        "LEAFLET_JS": _leaflet_js(),
        "DATA": _json_for_html(data),
    }
    template = _read(_TEMPLATE)
    # one pass: the inserted texts are never searched for placeholders
    return re.sub(r"\{\{(TITLE|LEAFLET_CSS|LEAFLET_JS|DATA)\}\}", lambda m: parts[m.group(1)], template)


def write_html(path: str, results: Sequence[dict], **kwargs) -> str:
    text = build_html(results, **kwargs)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ----------------------------------------------------------------------------------------------------------------
# static picture

_STATUS_TEXT = {
    STATUS_NO_BOUNDS: "no boundaries",
    STATUS_NOT_FOUND: "not found",
    STATUS_ERROR: "request failed",
    STATUS_SKIPPED: "not requested",
}


def _nice_length(metres: float) -> float:
    """The largest of 1, 2, 5 x 10^n that is not longer than `metres`."""
    if metres <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(metres))
    for factor in (5, 2, 1):
        if factor * power <= metres:
            return factor * power
    return power


def _format_length(metres: float) -> str:
    if metres >= 1000:
        return "%g km" % (metres / 1000)
    return "%g m" % metres


def _format_area(m2: Optional[float]) -> str:
    if not m2:
        return ""
    if m2 >= 10000:
        return ("%.2f" % (m2 / 10000)).rstrip("0").rstrip(".") + " ha"
    return "{:,.0f}".format(m2).replace(",", " ") + " m²"


def _signed_area(ring: Sequence[Sequence[float]]) -> float:
    return sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1)) / 2


def write_png(
    path: str,
    results: Sequence[dict],
    title: str = "Cadastral objects",
    note: Optional[str] = None,
    dpi: int = 150,
    generated: Optional[float] = None,
) -> str:
    """
    A static map: the outlines numbered as in the list, with a scale bar and a table of the numbers.
    It has no background map. Needs matplotlib (`pip install matplotlib`).
    """
    # the package logger is configured on DEBUG level - keep the internals of matplotlib out of the log
    for name in ("matplotlib", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        from matplotlib import patheffects
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import PathPatch
        from matplotlib.path import Path
    except ImportError as er:
        raise MapDependencyError("matplotlib is needed for the png map: pip install matplotlib") from er

    generated = time.time() if generated is None else generated
    items = build_items(results)
    counts = _counts(items)
    drawn = [item for item in items if item["geometry"]]

    fig = Figure(figsize=(14, 9), dpi=dpi, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.045, 0.07, 0.635, 0.80])
    table = fig.add_axes([0.70, 0.07, 0.285, 0.80])
    table.set_axis_off()
    ax.set_facecolor("#f3f5f2")
    halo = [patheffects.withStroke(linewidth=2.6, foreground="white")]

    summary = "%i requested: %i with boundaries" % (len(items), counts[STATUS_OK])
    for status in (STATUS_NO_BOUNDS, STATUS_NOT_FOUND, STATUS_ERROR, STATUS_SKIPPED):
        if counts[status]:
            summary += ", %i %s" % (counts[status], _STATUS_TEXT[status])
    fig.text(0.045, 0.955, title, fontsize=17, fontweight="bold", va="center", ha="left")
    fig.text(0.045, 0.918, summary, fontsize=10.5, va="center", ha="left", color="#333333")
    if note:
        fig.text(
            0.985,
            0.945,
            note,
            fontsize=10,
            va="center",
            ha="right",
            wrap=True,
            color="#5c4400",
            bbox={"boxstyle": "round,pad=0.5", "facecolor": "#fff8c5", "edgecolor": "#d4a72c"},
        )
    fig.text(
        0.045,
        0.022,
        _subtitle(generated) + ". No background map. For reference only: not an official extract from the register.",
        fontsize=8,
        color="#555555",
        va="center",
        ha="left",
    )

    bbox = merge_bboxes([item["bbox"] for item in drawn])
    if bbox:
        lon0, lat0 = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        k = math.cos(math.radians(lat0))
        metre = math.pi / 180 * 6371008.8

        def project(position):
            return ((position[0] - lon0) * k * metre, (position[1] - lat0) * metre)

        for item in drawn:
            geometry = item["geometry"]
            if item["kind"] == "polygon":
                vertices, codes = [], []
                for polygon in geometry["coordinates"]:
                    for index, ring in enumerate(polygon):
                        points = [project(p) for p in ring]
                        if points[0] != points[-1]:
                            points.append(points[0])
                        # the outer ring counter-clockwise, holes clockwise - so the holes stay empty
                        if (_signed_area(points) < 0) == (index == 0):
                            points.reverse()
                        vertices.extend(points)
                        codes.extend([Path.MOVETO] + [Path.LINETO] * (len(points) - 2) + [Path.CLOSEPOLY])
                shape = Path(vertices, codes)
                ax.add_patch(PathPatch(shape, facecolor=item["color"], alpha=0.55, edgecolor="none", zorder=2))
                ax.add_patch(PathPatch(shape, facecolor="none", edgecolor="#222222", linewidth=1.1, zorder=3))
            elif item["kind"] == "line":
                for line in geometry["coordinates"]:
                    xs, ys = zip(*[project(p) for p in line])
                    ax.plot(xs, ys, color="#222222", linewidth=3.2, zorder=2, solid_capstyle="round")
                    ax.plot(xs, ys, color=item["color"], linewidth=1.8, zorder=3, solid_capstyle="round")
            else:
                # only a location is known: a dashed ring, the number is written inside
                x, y = project(geometry["coordinates"])
                ax.scatter(
                    [x], [y], s=330, facecolors=item["color"], edgecolors="#222222", linewidths=1.2,
                    linestyles="dashed", alpha=0.9, zorder=4,
                )  # fmt: skip

        x1, y1 = project((bbox[0], bbox[1]))
        x2, y2 = project((bbox[2], bbox[3]))
        width, height = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
        # the map frame has a fixed shape: widen the shorter side of the extent, then add a margin
        frame = (0.635 * 14) / (0.80 * 9)
        if width / height < frame:
            width = height * frame
        else:
            height = width / frame
        width, height = width * 1.12, height * 1.12
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ax.set_xlim(cx - width / 2, cx + width / 2)
        ax.set_ylim(cy - height / 2, cy + height / 2)
        ax.set_aspect("equal", adjustable="box")

        # numbers: the ones that would cover an already placed number are left out (they stay in the table)
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        placed = []
        # the main number of every object first, then the numbers on the other contours of the objects
        spots = [(item, item["labels"][0], True) for item in drawn]
        spots += [(item, point, False) for item in drawn for point in item["labels"][1:]]
        for item, (lat, lon), main in spots:
            x, y = project((lon, lat))
            text = ax.text(
                x, y, str(item["n"]), fontsize=8.5, fontweight="bold" if main else "normal", ha="center",
                va="center", color="#111111", zorder=5, path_effects=halo,
            )  # fmt: skip
            box = text.get_window_extent(renderer).expanded(1.1, 1.1)
            if any(box.overlaps(other) for other in placed):
                text.remove()
                if main:
                    item["unlabelled"] = True
            else:
                placed.append(box)

        # scale bar
        bar = _nice_length(width * 0.22)
        bx, by = cx - width / 2 + width * 0.035, cy - height / 2 + height * 0.045
        ax.plot([bx, bx + bar], [by, by], color="white", linewidth=6, solid_capstyle="butt", zorder=6)
        ax.plot([bx, bx + bar], [by, by], color="#111111", linewidth=3, solid_capstyle="butt", zorder=7)
        ax.text(
            bx + bar / 2, by + height * 0.014, _format_length(bar), ha="center", va="bottom", fontsize=9, zorder=7,
            path_effects=halo,
        )  # fmt: skip
        # north arrow
        ax.annotate(
            "N", xy=(0.965, 0.955), xytext=(0.965, 0.865), xycoords="axes fraction", ha="center", va="center",
            fontsize=11, fontweight="bold", arrowprops={"arrowstyle": "-|>", "color": "#111111", "lw": 1.6},
            zorder=7, path_effects=halo,
        )  # fmt: skip

        # frame ticks in degrees
        def degrees(axis_range, origin, scale, count=4):
            step = (axis_range[1] - axis_range[0]) / (count + 1)
            ticks = [axis_range[0] + step * (i + 1) for i in range(count)]
            return ticks, ["%.4f°" % (origin + t / scale) for t in ticks]

        xticks, xlabels = degrees(ax.get_xlim(), lon0, k * metre)
        yticks, ylabels = degrees(ax.get_ylim(), lat0, metre)
        ax.set_xticks(xticks)
        ax.set_xticklabels(xlabels, fontsize=7.5, color="#555555")
        ax.set_yticks(yticks)
        ax.set_yticklabels(ylabels, fontsize=7.5, color="#555555", rotation=90, va="center")
        ax.tick_params(length=3, color="#888888")
    else:
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(
            0.5, 0.5,
            "No boundaries to show\n\nnone were received for the requested numbers:\nsee the list on the right",
            transform=ax.transAxes, ha="center", va="center", fontsize=14, color="#555555",
        )  # fmt: skip
    for spine in ax.spines.values():
        spine.set_edgecolor("#888888")

    # the table of numbers
    max_rows = 46
    rows = items[:max_rows]
    line = 1.0 / (max_rows + 2.5)
    y = 1.0
    table.text(0.0, y, "No.", fontsize=8.5, fontweight="bold", va="top")
    table.text(0.12, y, "Number", fontsize=8.5, fontweight="bold", va="top")
    table.text(1.0, y, "Area by the register", fontsize=8.5, fontweight="bold", va="top", ha="right")
    y -= line * 1.25
    for item in rows:
        located = bool(item["geometry"])
        color = "#111111" if located else "#8a8a8a"
        table.text(0.065, y, str(item["n"]), fontsize=8.5, va="top", ha="right", color=color)
        if located:
            table.add_patch(
                PathPatch(
                    Path([(0.085, y - line * 0.62), (0.105, y - line * 0.62), (0.105, y - line * 0.08),
                          (0.085, y - line * 0.08), (0.085, y - line * 0.62)]),
                    facecolor=item["color"], edgecolor="#222222", linewidth=0.6, transform=table.transAxes,
                )
            )  # fmt: skip
        table.text(0.12, y, item["code"], fontsize=8.5, va="top", color=color)
        if item["status"] == STATUS_OK:
            right, right_color = _format_area(item["info"]["area"]), "#111111"
            if item["length"]:
                right = right or _format_length(round(item["length"]))
        else:
            right, right_color = _STATUS_TEXT.get(item["status"], item["status"]), "#b3261e"
            if item["status"] == STATUS_SKIPPED:
                right_color = "#8a8a8a"
        table.text(1.0, y, right, fontsize=8.5, va="top", ha="right", color=right_color)
        y -= line
    if len(items) > max_rows:
        table.text(0.12, y, "… and %i more: see the csv report" % (len(items) - max_rows), fontsize=8.5, va="top",
                   color="#555555")  # fmt: skip
        y -= line
    if any(item.get("unlabelled") for item in drawn):
        table.text(
            0.0, max(y - line * 0.5, 0.0), "Some numbers are left out on the map where they would\noverlap. "
            "The interactive map shows every object.", fontsize=7.5, va="top", color="#555555",
        )  # fmt: skip
    table.set_xlim(0, 1)
    table.set_ylim(0, 1)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=dpi, facecolor="white")
    return path


__all__ = [
    "DEFAULT_BASEMAPS",
    "MapDependencyError",
    "build_html",
    "build_items",
    "load_features",
    "results_from_features",
    "write_html",
    "write_png",
]
