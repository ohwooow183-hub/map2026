import math

import pytest
from rosreestr2coord.geometry import (
    geometry_area_m2,
    geometry_bbox,
    geometry_center,
    geometry_kind,
    geometry_length_m,
    has_shape,
    iter_polygons,
    line_midpoint,
    merge_bboxes,
    polygon_label_point,
)
from rosreestr2coord.utils import transform_to_wgs

pytestmark = pytest.mark.unit

LON, LAT = 38.0, 55.2


def rectangle(lon, lat, width_deg, height_deg):
    return [
        [lon, lat],
        [lon + width_deg, lat],
        [lon + width_deg, lat + height_deg],
        [lon, lat + height_deg],
        [lon, lat],
    ]


def inside(point, ring):
    """Even-odd rule."""
    x, y = point
    result = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            result = not result
    return result


def test_kind_of_geometry():
    polygon = {"type": "Polygon", "coordinates": [rectangle(LON, LAT, 0.001, 0.001)]}
    line = {"type": "LineString", "coordinates": [[LON, LAT], [LON + 0.01, LAT]]}
    point = {"type": "Point", "coordinates": [LON, LAT]}

    assert geometry_kind(polygon) == "polygon"
    assert geometry_kind(line) == "line"
    assert geometry_kind(point) == "point"
    assert geometry_kind(None) == ""
    assert geometry_kind({"type": "Polygon", "coordinates": []}) == ""

    # only an outline or a line shows where the object lies
    assert has_shape(polygon) and has_shape(line)
    assert not has_shape(point) and not has_shape(None)


def test_area_of_a_small_rectangle_on_the_ellipsoid():
    d = 0.001
    geometry = {"type": "Polygon", "coordinates": [rectangle(LON, LAT, d, d)]}

    # radii of curvature of WGS84 in the middle of the rectangle
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    phi = math.radians(LAT + d / 2)
    w = math.sqrt(1 - e2 * math.sin(phi) ** 2)
    meridian, prime_vertical = a * (1 - e2) / w**3, a / w
    expected = (meridian * math.radians(d)) * (prime_vertical * math.cos(phi) * math.radians(d))

    assert geometry_area_m2(geometry) == pytest.approx(expected, rel=1e-6)


def test_area_does_not_depend_on_ring_direction_and_subtracts_holes():
    outer = rectangle(LON, LAT, 0.002, 0.002)
    hole = rectangle(LON + 0.0005, LAT + 0.0005, 0.001, 0.001)
    full = geometry_area_m2({"type": "Polygon", "coordinates": [outer]})
    reversed_ring = geometry_area_m2({"type": "Polygon", "coordinates": [outer[::-1]]})
    with_hole = geometry_area_m2({"type": "Polygon", "coordinates": [outer, hole]})

    assert reversed_ring == pytest.approx(full)
    assert with_hole == pytest.approx(full * 0.75, rel=1e-4)


def test_area_of_a_real_plot_matches_the_register(sample):
    """The outline of 50:33:0000000:98325 gives the area recorded for it in the register (22 318 m²)."""
    feature = transform_to_wgs(sample)
    registered = sample["properties"]["options"]["specified_area"]

    assert len(list(iter_polygons(feature["geometry"]))) == 3
    assert geometry_area_m2(feature["geometry"]) == pytest.approx(registered, abs=2)


def test_area_of_lines_and_points_is_zero():
    assert geometry_area_m2({"type": "LineString", "coordinates": [[LON, LAT], [LON + 0.01, LAT]]}) == 0
    assert geometry_area_m2({"type": "Point", "coordinates": [LON, LAT]}) == 0
    assert geometry_area_m2(None) == 0


def test_bbox():
    geometry = {"type": "MultiPolygon", "coordinates": [[rectangle(1, 2, 3, 4)], [rectangle(10, 20, 1, 1)]]}
    assert geometry_bbox(geometry) == (1, 2, 11, 21)
    assert geometry_bbox(None) is None
    assert merge_bboxes([(1, 2, 3, 4), None, (0, 3, 2, 9)]) == (0, 2, 3, 9)
    assert merge_bboxes([None]) is None


def test_label_point_is_inside_a_concave_outline():
    # a "U": the centroid of the outline lies in the gap between its arms
    d = 0.001
    ring = [
        [LON, LAT],
        [LON + 3 * d, LAT],
        [LON + 3 * d, LAT + 3 * d],
        [LON + 2 * d, LAT + 3 * d],
        [LON + 2 * d, LAT + d],
        [LON + d, LAT + d],
        [LON + d, LAT + 3 * d],
        [LON, LAT + 3 * d],
        [LON, LAT],
    ]
    centroid = [LON + 1.5 * d, LAT + 2 * d]
    assert not inside(centroid, ring)

    point = polygon_label_point([ring])

    assert inside(point, ring)


def test_label_point_avoids_a_hole():
    outer = rectangle(LON, LAT, 0.003, 0.003)
    hole = rectangle(LON + 0.0005, LAT + 0.0005, 0.002, 0.002)

    point = polygon_label_point([outer, hole])

    assert inside(point, outer) and not inside(point, hole)


def test_center_of_a_multipolygon_is_inside_its_largest_part(sample):
    geometry = transform_to_wgs(sample)["geometry"]
    largest = max(iter_polygons(geometry), key=lambda polygon: len(polygon[0]))

    center = geometry_center(geometry)

    assert inside(center, largest[0])


def test_length_and_middle_of_a_line():
    line = [[LON, LAT], [LON, LAT + 0.01], [LON, LAT + 0.03]]
    geometry = {"type": "LineString", "coordinates": line}

    # 0.03 degrees of a meridian are about 3.3 km
    assert geometry_length_m(geometry) == pytest.approx(0.03 * 111195, rel=2e-3)
    assert line_midpoint(line) == pytest.approx([LON, LAT + 0.015])
    assert geometry_center(geometry) == pytest.approx([LON, LAT + 0.015])
    assert geometry_length_m({"type": "Point", "coordinates": [LON, LAT]}) == 0


def test_center_of_a_point_and_of_nothing():
    assert geometry_center({"type": "Point", "coordinates": [LON, LAT]}) == [LON, LAT]
    assert geometry_center(None) is None
