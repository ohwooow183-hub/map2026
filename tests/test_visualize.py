import json
import re
import struct
import sys

import pytest
from rosreestr2coord.batch import make_result
from rosreestr2coord.utils import transform_to_wgs
from rosreestr2coord.visualize import (
    DEFAULT_BASEMAPS,
    MapDependencyError,
    build_html,
    build_items,
    load_features,
    results_from_features,
    write_html,
    write_png,
)

from .nspd_mock import load_sample, make_feature

pytestmark = pytest.mark.unit


def found(code, **kwargs):
    return make_result(code, "ok", transform_to_wgs(make_feature(code, **kwargs)), retrieved=1762776000.0)


@pytest.fixture
def results(sample):
    road = transform_to_wgs(make_feature("99:01:0000000:1007"))
    road["geometry"] = {"type": "LineString", "coordinates": [[38.00, 55.20], [38.01, 55.20], [38.03, 55.21]]}
    point = transform_to_wgs(make_feature("99:01:0101001:13", geometry="Point", x=4233200.0))
    return [
        found("99:01:0101001:11", x=4233000.0),
        found("99:01:0101001:12", x=4233070.0),
        make_result("99:01:0101001:13", "no_bounds", point, "the object is in the registry, without boundaries"),
        found("99:01:0101002:23", x=4233000.0, y=7405100.0),
        make_result("99:01:0000000:1007", "ok", road),
        make_result("50:33:0000000:98325", "ok", transform_to_wgs(sample)),
        make_result("99:01:0101001:99", "not_found", None, "the service has no object with this number"),
        make_result("99:01:0101001:98", "error", None, "HTTP 502: Bad Gateway"),
        make_result("99:01:0101001:97", "skipped"),
    ]


def page_data(page):
    match = re.search(r'<script type="application/json" id="r2c-data">(.*?)</script>', page, re.S)
    return json.loads(match.group(1))


def test_items_describe_every_requested_number(results):
    items = build_items(results)

    assert [item["code"] for item in items] == [r["code"] for r in results]
    assert [item["n"] for item in items] == list(range(1, 10))
    assert [item["status"] for item in items] == [
        "ok", "ok", "no_bounds", "ok", "ok", "ok", "not_found", "error", "skipped",
    ]  # fmt: skip
    assert [item["kind"] for item in items] == ["polygon", "polygon", "point", "polygon", "line", "polygon", "", "", ""]
    assert [item["short"] for item in items[:3]] == [":11", ":12", ":13"]
    # nothing is drawn for the numbers without data
    assert all(item["geometry"] is None and item["center"] is None for item in items[6:])


def test_objects_of_one_quarter_share_a_colour(results):
    items = build_items(results)
    by_code = {item["code"]: item for item in items}

    assert by_code["99:01:0101001:11"]["group"] == "99:01:0101001"
    assert by_code["99:01:0101001:11"]["color"] == by_code["99:01:0101001:12"]["color"]
    assert by_code["99:01:0101001:11"]["color"] == by_code["99:01:0101001:13"]["color"]
    colours = {by_code[code]["color"] for code in ("99:01:0101001:11", "99:01:0101002:23", "99:01:0000000:1007")}
    assert len(colours) == 3


def test_measures_of_the_objects(results):
    by_code = {item["code"]: item for item in build_items(results)}
    plot = by_code["50:33:0000000:98325"]
    road = by_code["99:01:0000000:1007"]

    assert plot["parts"] == 3
    assert plot["area_outline"] == pytest.approx(22318, abs=2)
    assert plot["info"]["area"] == 22318
    # a label for every contour, the main one first
    assert len(plot["labels"]) == 3 and plot["labels"][0] == plot["center"]
    lat, lon = plot["center"]
    assert 55.1 < lat < 55.4 and 37.9 < lon < 38.2

    assert road["area_outline"] is None
    assert 1900 < road["length"] < 2400


def test_a_point_is_never_shown_as_boundaries():
    point = transform_to_wgs(make_feature("99:01:0101001:13", geometry="Point"))

    (item,) = build_items([make_result("99:01:0101001:13", "ok", point)])

    assert item["status"] == "no_bounds"


def test_page_is_self_contained(results):
    page = build_html(results, title="My plots")

    assert "<title>My plots</title>" in page
    assert not re.search(r"<script[^>]+src=", page)
    assert not re.search(r"<link[^>]+href=", page)
    assert "{{" not in page.split("<script", 1)[0]
    # Leaflet is inside the page, with the images of its controls
    assert "Leaflet 1.9.4, a JS library for interactive maps" in page
    style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    assert "@import" not in style
    references = [u for u in re.findall(r"url\(([^)]*)\)", style) if not u.startswith(("data:", "#"))]
    assert references == []


def test_page_carries_the_data(results):
    data = page_data(build_html(results, title="My plots", note="A remark", generated=1762776000.0))

    assert data["title"] == "My plots" and data["note"] == "A remark"
    assert "2025-11-10 12:00 UTC" in data["subtitle"]
    assert data["counts"] == {"ok": 5, "no_bounds": 1, "not_found": 1, "error": 1, "skipped": 1}
    assert [item["code"] for item in data["items"]] == [r["code"] for r in results]
    assert [g["id"] for g in data["groups"]] == ["99:01:0101001", "99:01:0101002", "99:01:0000000", "50:33:0000000"]
    assert [b["name"] for b in data["basemaps"]] == [b["name"] for b in DEFAULT_BASEMAPS]
    plot = data["items"][5]
    assert plot["geometry"]["type"] == "MultiPolygon" and len(plot["geometry"]["coordinates"]) == 3
    assert ["Cadastral number", "50:33:0000000:98325"] in plot["rows"]


def test_text_from_the_service_can_not_break_the_page():
    evil = "</script><script>alert(1)</script><img src=x onerror=alert(2)>"
    feature = transform_to_wgs(make_feature("99:01:0101001:11", readable_address=evil))

    page = build_html([make_result("99:01:0101001:11", "ok", feature, message=evil)], title=evil)

    assert evil not in page
    assert page.count("</script>") == 3
    assert page_data(page)["items"][0]["info"]["address"] == evil
    assert "<title>&lt;/script&gt;" in page


def test_page_without_any_boundaries_still_lists_the_numbers():
    results = [make_result("99:01:0101001:%d" % n, "skipped") for n in range(3)]

    data = page_data(build_html(results))

    assert data["counts"]["skipped"] == 3 and data["groups"] == []
    assert len(data["items"]) == 3


def test_basemaps_can_be_replaced(results):
    assert page_data(build_html(results, basemaps=[]))["basemaps"] == []


def test_write_html(results, tmp_path):
    path = write_html(str(tmp_path / "map" / "mylist.html"), results, title="My plots")

    with open(path, encoding="utf-8") as f:
        assert page_data(f.read())["title"] == "My plots"


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_write_png(results, tmp_path):
    pytest.importorskip("matplotlib")

    path = write_png(str(tmp_path / "map" / "mylist.png"), results, title="My plots")

    width, height = png_size(path)
    assert width > 1000 and height > 600


def test_write_png_without_any_boundaries(tmp_path):
    pytest.importorskip("matplotlib")
    results = [make_result("99:01:0101001:%d" % n, "error", message="HTTP 502") for n in range(3)]

    path = write_png(str(tmp_path / "empty.png"), results)

    assert png_size(path)[0] > 1000


def test_png_needs_matplotlib(results, tmp_path, monkeypatch):
    for name in [n for n in sys.modules if n == "matplotlib" or n.startswith("matplotlib.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "matplotlib", None)

    with pytest.raises(MapDependencyError, match="pip install matplotlib"):
        write_png(str(tmp_path / "mylist.png"), results)


def test_features_are_read_from_saved_files(results, sample, tmp_path):
    folder = tmp_path / "geojson"
    folder.mkdir()
    plots = [r["feature"] for r in results[:2]]
    for feature in plots:
        (folder / ("%s.geojson" % feature["properties"]["label"].replace(":", "_"))).write_text(
            json.dumps(feature, ensure_ascii=False), encoding="utf-8"
        )
    # the file with all objects repeats them
    collection = {"type": "FeatureCollection", "features": plots}
    (folder / "mylist.geojson").write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    (folder / "notes.txt").write_text("not a map", encoding="utf-8")
    # a raw answer of the service, in metres
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(load_sample(), ensure_ascii=False), encoding="utf-8")

    features = load_features([str(folder), str(raw)])

    assert sorted(f["properties"]["label"] for f in features) == [
        "50:33:0000000:98325", "99:01:0101001:11", "99:01:0101001:12",
    ]  # fmt: skip
    plot = next(f for f in features if f["properties"]["label"] == "50:33:0000000:98325")
    lon, lat = plot["geometry"]["coordinates"][0][0][0]
    assert 37.9 < lon < 38.2 and 55.1 < lat < 55.4


def test_file_that_is_not_json_is_reported(tmp_path):
    path = tmp_path / "broken.geojson"
    path.write_text("<html>", encoding="utf-8")

    with pytest.raises(ValueError, match="broken.geojson"):
        load_features([str(path)])


def test_results_from_files_follow_the_list(results):
    features = [r["feature"] for r in results[:2]]

    listed = results_from_features(features, ["99:01:0101001:12", "99:01:0101001:77", " 99:01:0101001:11 "])
    unlisted = results_from_features(features)

    assert [(r["code"], r["status"]) for r in listed] == [
        ("99:01:0101001:12", "ok"), ("99:01:0101001:77", "skipped"), ("99:01:0101001:11", "ok"),
    ]  # fmt: skip
    assert "no data" in listed[1]["message"] and listed[1]["feature"] is None
    assert [(r["code"], r["status"]) for r in unlisted] == [("99:01:0101001:11", "ok"), ("99:01:0101001:12", "ok")]
