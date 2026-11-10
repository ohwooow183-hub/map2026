import copy
import csv
import json
import xml.etree.ElementTree as ET

import pytest
from rosreestr2coord.export import (
    CSV_COLUMNS,
    batch_csv_output,
    batch_json_output,
    coords2kml,
    csv_row,
    feature2kml,
)
from rosreestr2coord.utils import transform_to_wgs

from .nspd_mock import make_feature

pytestmark = pytest.mark.unit

KML = {"k": "http://www.opengis.net/kml/2.2"}


def kml_root(tree, tmp_path):
    """Write the document the way the console does and read it back."""
    path = tmp_path / "out.kml"
    tree.write(str(path), encoding="UTF-8", xml_declaration=True)
    return ET.parse(str(path)).getroot()


def ring_of(element):
    return [tuple(float(v) for v in position.split(",")) for position in element.text.split()]


def test_kml_of_a_multipolygon_has_a_polygon_per_part(sample, tmp_path):
    feature = transform_to_wgs(sample)

    root = kml_root(feature2kml(feature), tmp_path)

    polygons = root.findall(".//k:Polygon", KML)
    assert len(polygons) == 3
    assert root.find(".//k:Placemark/k:name", KML).text == "50:33:0000000:98325"
    for polygon, part in zip(polygons, feature["geometry"]["coordinates"]):
        assert len(polygon.findall("k:outerBoundaryIs", KML)) == 1
        assert len(polygon.findall("k:innerBoundaryIs", KML)) == len(part) - 1
        ring = ring_of(polygon.find("k:outerBoundaryIs/k:LinearRing/k:coordinates", KML))
        assert ring == [tuple(position) for position in part[0]]
        assert ring[0] == ring[-1]


def test_kml_does_not_change_the_feature(sample):
    feature = transform_to_wgs(sample)
    before = copy.deepcopy(feature)
    feature2kml(feature)
    assert feature == before


def test_kml_of_a_polygon_with_a_hole(tmp_path):
    outer = [[35.0, 54.0], [35.01, 54.0], [35.01, 54.01], [35.0, 54.01], [35.0, 54.0]]
    hole = [[35.002, 54.002], [35.004, 54.002], [35.004, 54.004], [35.002, 54.004], [35.002, 54.002]]
    feature = {
        "type": "Feature",
        "properties": {"label": "x"},
        "geometry": {"type": "Polygon", "coordinates": [outer, hole]},
    }

    root = kml_root(feature2kml(feature), tmp_path)

    assert len(root.findall(".//k:Polygon", KML)) == 1
    assert len(root.findall(".//k:outerBoundaryIs", KML)) == 1
    assert len(root.findall(".//k:innerBoundaryIs", KML)) == 1


def test_kml_closes_open_rings_without_touching_the_input(tmp_path):
    ring = [[35.0, 54.0], [35.01, 54.0], [35.01, 54.01]]
    coords = [[ring]]

    root = kml_root(coords2kml(coords, {"label": "x"}), tmp_path)

    written = ring_of(root.find(".//k:coordinates", KML))
    assert len(written) == 4 and written[0] == written[-1]
    assert len(ring) == 3


def test_kml_of_a_line_and_of_a_point(tmp_path):
    line = {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "LineString", "coordinates": [[35.0, 54.0], [35.1, 54.1]]},
    }
    point = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [35.0, 54.0]}}

    assert len(kml_root(feature2kml(line), tmp_path).findall(".//k:LineString", KML)) == 1
    assert len(kml_root(feature2kml(point), tmp_path).findall(".//k:Point", KML)) == 1
    assert feature2kml({"type": "Feature", "properties": {}, "geometry": None}) is False


def test_csv_row_of_a_found_object(sample):
    feature = transform_to_wgs(sample)

    row = csv_row("50:33:0000000:98325", feature, "ok")

    assert list(row) == CSV_COLUMNS
    assert row["code"] == "50:33:0000000:98325"
    assert row["area"] == "22318"
    assert row["status"] == "ok"
    assert row["object"] == "Земельный участок"
    assert float(row["area_by_outline"]) == pytest.approx(22318, abs=2)
    assert 55.1 < float(row["lat"]) < 55.4 and 37.9 < float(row["lon"]) < 38.2
    # the coordinates column keeps the order of the old report: [lat, lon]
    lat, lon = json.loads(row["coordinates"])[0][0][0]
    assert [lon, lat] == feature["geometry"]["coordinates"][0][0][0]


def test_csv_row_of_a_missing_object():
    row = csv_row("99:01:0101001:15", None, "not_found", "the service has no object with this number")
    assert row["code"] == "99:01:0101001:15" and row["status"] == "not_found"
    assert row["note"] == "the service has no object with this number"
    assert row["area"] == row["coordinates"] == row["lat"] == ""


def test_csv_report_lists_every_code(sample, tmp_path):
    rows = [
        csv_row("50:33:0000000:98325", transform_to_wgs(sample), "ok"),
        csv_row("99:01:0101001:15", None, "not_found", "nothing"),
        csv_row("99:01:0101001:16", None, "error", "HTTP 502"),
    ]

    path = batch_csv_output(str(tmp_path), rows, "mylist")

    raw = open(path, "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf")
    with open(path, encoding="utf-8", newline="") as f:
        read = list(csv.DictReader(f))
    assert [r["code"] for r in read] == ["50:33:0000000:98325", "99:01:0101001:15", "99:01:0101001:16"]
    assert [r["status"] for r in read] == ["ok", "not_found", "error"]
    assert read[0]["address"].endswith("г.о. Ступино")
    assert b"\r\n\r\n" not in raw


def test_geojson_collection_is_plain_wgs84(tmp_path):
    features = [transform_to_wgs(make_feature("99:01:0101001:%d" % n, x=4233000 + n * 100)) for n in (11, 12)]

    path = batch_json_output(str(tmp_path), features, "mylist")

    with open(path, encoding="utf-8") as f:
        collection = json.load(f)
    assert collection["type"] == "FeatureCollection"
    assert [f["properties"]["label"] for f in collection["features"]] == ["99:01:0101001:11", "99:01:0101001:12"]
    for feature in collection["features"]:
        lon, lat = feature["geometry"]["coordinates"][0][0]
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert "crs" not in feature["geometry"]
