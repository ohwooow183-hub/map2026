"""The command line, run against the local stand-in of the service."""

import importlib
import importlib.metadata
import importlib.util
import json
import os
import re

import pytest
from rosreestr2coord import console
from rosreestr2coord.console import getopts, run_console

from .nspd_mock import make_feature

pytestmark = pytest.mark.unit

HAS_MATPLOTLIB = importlib.util.find_spec("matplotlib") is not None


def page_data(path):
    with open(path, encoding="utf-8") as f:
        page = f.read()
    return json.loads(re.search(r'<script type="application/json" id="r2c-data">(.*?)</script>', page, re.S).group(1))


@pytest.fixture
def opened(monkeypatch):
    """What would be opened in the browser."""
    urls = []
    monkeypatch.setattr(console.webbrowser, "open", lambda url: urls.append(url))
    return urls


@pytest.fixture
def listed(nspd, no_wait, workdir, sample):
    """A list file of four numbers; the stand-in knows three of them, one has no boundaries."""
    nspd.add(sample)
    nspd.add(make_feature("99:01:0101001:11", x=4233000.0))
    nspd.add(make_feature("99:01:0101001:13", geometry="Point", x=4233200.0))
    # as Notepad saves it: with a byte order mark and CRLF
    with open("plots.txt", "w", encoding="utf-8-sig", newline="") as f:
        f.write("50:33:0000000:98325\r\n99:01:0101001:11\r\n\r\n99:01:0101001:13\r\n99:01:0101001:99\r\n")
    return nspd


def test_options():
    opt = getopts(["-l", "plots.txt", "-m"])
    assert opt.list == "plots.txt" and opt.map and not opt.display and opt.map_from is None
    assert opt.area_type == 1 and opt.delay == 1 and opt.timeout == 5 and not opt.refresh

    opt = getopts(["--map-from", "a.geojson", "output/geojson", "--title", "My plots", "-d"])
    assert opt.map_from == ["a.geojson", "output/geojson"] and opt.title == "My plots" and opt.display

    opt = getopts(["-c", "38:06:144003:4723", "-T", "20", "-u", "http://10.0.0.1:3128", "-r"])
    assert opt.code == "38:06:144003:4723" and opt.timeout == 20 and opt.proxy_url == "http://10.0.0.1:3128"
    assert opt.refresh


def test_list_with_map(listed, capsys):
    run_console(getopts(["-l", "plots.txt", "--map"]))

    out = capsys.readouterr().out
    assert listed.requested_codes == ["50:33:0000000:98325", "99:01:0101001:11", "99:01:0101001:13", "99:01:0101001:99"]
    assert os.path.isfile("output/csv/plots.csv")
    assert os.path.isfile("output/geojson/plots.geojson")
    html = os.path.abspath("output/map/plots.html")
    assert "map - %s" % html in out

    data = page_data(html)
    assert data["title"] == "Cadastral objects: plots"
    assert [(item["code"], item["status"]) for item in data["items"]] == [
        ("50:33:0000000:98325", "ok"),
        ("99:01:0101001:11", "ok"),
        ("99:01:0101001:13", "no_bounds"),
        ("99:01:0101001:99", "not_found"),
    ]
    if HAS_MATPLOTLIB:
        assert os.path.isfile("output/map/plots.png")
        assert "png - %s" % os.path.abspath("output/map/plots.png") in out


def test_list_without_map_makes_no_map(listed):
    run_console(getopts(["-l", "plots.txt"]))

    assert os.path.isfile("output/csv/plots.csv")
    assert not os.path.exists("output/map")


def test_display_opens_the_map_in_the_browser(listed, opened):
    run_console(getopts(["-l", "plots.txt", "-d", "-o", "out"]))

    (url,) = opened
    assert url.startswith("file://") and url.endswith("/out/map/plots.html")


def test_png_is_optional(listed, monkeypatch, capsys):
    def no_matplotlib(*args, **kwargs):
        raise console.MapDependencyError("matplotlib is needed for the png map: pip install matplotlib")

    monkeypatch.setattr(console, "write_png", no_matplotlib)

    run_console(getopts(["-l", "plots.txt", "-m"]))

    assert os.path.isfile("output/map/plots.html")
    assert "png - skipped: matplotlib is needed" in capsys.readouterr().out


def test_single_code_with_map(listed, capsys):
    run_console(getopts(["-c", "50:33:0000000:98325", "-m"]))

    assert os.path.isfile("output/geojson/50_33_0000000_98325.geojson")
    assert os.path.isfile("output/kml/50_33_0000000_98325.kml")
    (item,) = page_data("output/map/50_33_0000000_98325.html")["items"]
    assert item["code"] == "50:33:0000000:98325" and item["status"] == "ok" and item["parts"] == 3


def test_single_code_that_is_not_found(listed, capsys):
    run_console(getopts(["-c", "99:01:0101001:99", "-m"]))

    assert "Nothing found" in capsys.readouterr().out
    assert not os.path.exists("output/geojson")
    (item,) = page_data("output/map/99_01_0101001_99.html")["items"]
    assert item["status"] == "not_found" and item["geometry"] is None


def test_timeout_and_proxy_reach_the_request(listed, monkeypatch):
    seen = {}

    def fake_request(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr("rosreestr2coord.parser.make_request", fake_request)

    run_console(getopts(["-c", "99:01:0101001:11", "-T", "20", "-u", "http://10.0.0.1:3128"]))

    assert seen["timeout"] == 20 and seen["proxy_url"] == "http://10.0.0.1:3128"


def test_map_from_saved_files_makes_no_requests(listed, opened):
    run_console(getopts(["-l", "plots.txt"]))
    del listed.requests[:]

    run_console(getopts(["--map-from", "output/geojson", "-l", "plots.txt", "-o", "again", "--title", "My plots"]))

    assert listed.requests == []
    data = page_data("again/map/plots.html")
    assert data["title"] == "My plots"
    assert [(item["code"], item["status"]) for item in data["items"]] == [
        ("50:33:0000000:98325", "ok"),
        ("99:01:0101001:11", "ok"),
        ("99:01:0101001:13", "no_bounds"),
        # the saved files can not tell why a number is missing
        ("99:01:0101001:99", "skipped"),
    ]
    assert opened == []


def test_map_from_a_file_without_a_list(listed):
    run_console(getopts(["-l", "plots.txt"]))

    run_console(getopts(["--map-from", "output/geojson/plots.geojson", "-o", "again"]))

    data = page_data("again/map/plots.html")
    assert [item["code"] for item in data["items"]] == ["50:33:0000000:98325", "99:01:0101001:11", "99:01:0101001:13"]


def test_map_from_nothing(workdir, capsys):
    os.makedirs("empty")

    run_console(getopts(["--map-from", "empty"]))

    assert "nothing to show" in capsys.readouterr().out
    assert not os.path.exists("output")


def test_nothing_to_do(workdir, capsys):
    run_console(getopts([]))
    assert "Nothing to do" in capsys.readouterr().out


def test_works_from_a_source_tree_without_installation(monkeypatch):
    def not_installed(name):
        raise importlib.metadata.PackageNotFoundError(name)

    try:
        monkeypatch.setattr(importlib.metadata, "version", not_installed)
        assert importlib.reload(console).__version__ == "unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(console)
