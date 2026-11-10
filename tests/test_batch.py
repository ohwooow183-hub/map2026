"""List mode: every code gets a result, the files are written, an interrupted run can be continued."""

import csv
import json
import os

import pytest
from rosreestr2coord import batch
from rosreestr2coord.batch import batch_parser, read_codes

from .nspd_mock import FORBIDDEN_PAGE, make_feature

pytestmark = pytest.mark.unit


def codes_of(prefix, numbers):
    return ["%s:%d" % (prefix, n) for n in numbers]


def statuses(results):
    return [r["status"] for r in results]


@pytest.fixture
def village(nspd, no_wait):
    """Five plots of a made up quarter are known to the stand-in."""
    for i, code in enumerate(codes_of("99:01:0101001", range(11, 16))):
        nspd.add(make_feature(code, x=4233000.0 + i * 70))
    return nspd


def test_read_codes():
    lines = [
        "\ufeff38:06:144003:4723\r\n",
        "  «38:06:144003:4724»;\n",
        "\n",
        "# a comment\n",
        "38:06:144003:4723\n",
        "38:06:144004:23, 38:06:144004:24;38:06:144004:25 38:06:144004:26\n",
        "38:06:000000:1006  # a road\n",
        "Иркутская область, Иркутск\n",
    ]
    assert read_codes(lines) == [
        "38:06:144003:4723",
        "38:06:144003:4724",
        "38:06:144004:23",
        "38:06:144004:24",
        "38:06:144004:25",
        "38:06:144004:26",
        "38:06:000000:1006",
        "Иркутская область, Иркутск",
    ]


def test_every_code_gets_a_result_in_the_order_of_the_list(village, workdir, sample):
    village.add(sample)
    village.add(make_feature("99:01:0101001:20", geometry="Point"))
    village.respond("99:01:0101001:21", 502)
    codes = ["99:01:0101001:12", "50:33:0000000:98325", "99:01:0101001:20", "99:01:0101001:99", "99:01:0101001:21"]

    results = batch_parser(codes, output="output", file_name="mylist", delay=0)

    assert [r["code"] for r in results] == codes
    assert statuses(results) == ["ok", "ok", "no_bounds", "not_found", "error"]
    assert results[0]["feature"]["properties"]["label"] == "99:01:0101001:12"
    assert results[3]["feature"] is None and results[4]["feature"] is None
    assert "502" in results[4]["message"]
    assert all(r["retrieved"] for r in results[:3]) and not any(r["cached"] for r in results)


def test_list_mode_writes_the_files(village, workdir, sample):
    village.add(sample)
    codes = ["99:01:0101001:11", "50:33:0000000:98325", "99:01:0101001:99"]

    batch_parser(codes, output="output", file_name="mylist", delay=0)

    assert sorted(os.listdir("output/geojson")) == [
        "50_33_0000000_98325.geojson",
        "99_01_0101001_11.geojson",
        "mylist.geojson",
    ]
    with open("output/geojson/mylist.geojson", encoding="utf-8") as f:
        collection = json.load(f)
    assert [f["properties"]["label"] for f in collection["features"]] == ["99:01:0101001:11", "50:33:0000000:98325"]
    lon, lat = collection["features"][1]["geometry"]["coordinates"][0][0][0]
    assert 37.9 < lon < 38.2 and 55.1 < lat < 55.4

    with open("output/csv/mylist.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["code"] for r in rows] == codes
    assert [r["status"] for r in rows] == ["ok", "ok", "not_found"]
    assert rows[1]["area"] == "22318"


def test_second_run_requests_only_what_is_missing(village, workdir):
    village.respond("99:01:0101001:13", 500, times=1)
    codes = codes_of("99:01:0101001", (11, 12, 13, 99))

    first = batch_parser(codes, output="output", file_name="mylist", delay=0)
    assert statuses(first) == ["ok", "ok", "error", "not_found"]
    del village.requests[:]

    second = batch_parser(codes, output="output", file_name="mylist", delay=0)

    assert statuses(second) == ["ok", "ok", "ok", "not_found"]
    # saved objects are read from the files; "not found" is asked again - the object may have appeared
    assert village.requested_codes == ["99:01:0101001:13", "99:01:0101001:99"]
    assert [r["cached"] for r in second] == [True, True, False, False]
    assert second[0]["feature"] == first[0]["feature"]


def test_refresh_requests_everything_again(village, workdir):
    codes = codes_of("99:01:0101001", (11, 12))
    batch_parser(codes, output="output", file_name="mylist", delay=0)
    del village.requests[:]

    batch_parser(codes, output="output", file_name="mylist", delay=0, use_cache=False)

    assert village.requested_codes == codes


def test_saved_file_of_another_object_is_not_trusted(village, workdir):
    os.makedirs("output/geojson")
    with open("output/geojson/99_01_0101001_11.geojson", "w", encoding="utf-8") as f:
        json.dump(make_feature("99:01:0101001:77"), f)

    results = batch_parser(["99:01:0101001:11"], output="output", file_name="mylist", delay=0)

    assert village.requested_codes == ["99:01:0101001:11"]
    assert results[0]["feature"]["properties"]["label"] == "99:01:0101001:11"


def test_stops_at_once_when_access_is_denied(village, workdir, capsys):
    village.respond("99:01:0101001:12", 403, body=FORBIDDEN_PAGE, content_type="text/html")
    codes = codes_of("99:01:0101001", range(11, 16))

    results = batch_parser(codes, output="output", file_name="mylist", delay=0)

    assert statuses(results) == ["ok", "error", "skipped", "skipped", "skipped"]
    assert village.requested_codes == codes[:2]
    out = capsys.readouterr().out
    assert "Stopped: access denied" in out and "Run the same command again" in out
    with open("output/csv/mylist.csv", encoding="utf-8", newline="") as f:
        assert [r["status"] for r in csv.DictReader(f)] == ["ok", "error", "skipped", "skipped", "skipped"]


def test_stops_after_three_failures_in_a_row(village, workdir):
    codes = codes_of("99:01:0101001", range(11, 16)) + codes_of("99:01:0101001", range(31, 36))
    for code in codes[5:]:
        village.respond(code, 503)

    results = batch_parser(codes, output="output", file_name="mylist", delay=0)

    assert statuses(results) == ["ok"] * 5 + ["error"] * 3 + ["skipped"] * 2
    assert village.requested_codes == codes[:8]


def test_single_failures_do_not_stop_the_run(village, workdir):
    village.respond("99:01:0101001:12", 500)
    village.respond("99:01:0101001:14", 500)
    codes = codes_of("99:01:0101001", range(11, 16))

    results = batch_parser(codes, output="output", file_name="mylist", delay=0)

    assert statuses(results) == ["ok", "error", "ok", "error", "ok"]


def test_waits_and_repeats_when_asked_to_slow_down(village, workdir, no_wait):
    village.respond("99:01:0101001:12", 429, times=1)
    codes = codes_of("99:01:0101001", (11, 12, 13))

    results = batch_parser(codes, output="output", file_name="mylist", delay=0)

    assert statuses(results) == ["ok", "ok", "ok"]
    assert village.requested_codes == ["99:01:0101001:11", "99:01:0101001:12", "99:01:0101001:12", "99:01:0101001:13"]
    assert batch.BACKOFF_429[0] in no_wait


def test_gives_up_when_the_service_keeps_asking_to_slow_down(village, workdir, no_wait):
    village.respond("99:01:0101001:11", 429)

    results = batch_parser(["99:01:0101001:11"], output="output", file_name="mylist", delay=0)

    assert statuses(results) == ["error"]
    assert "429" in results[0]["message"]
    assert len(village.requests) == 1 + len(batch.BACKOFF_429)


def test_delay_is_kept_between_requests_but_not_for_saved_objects(village, workdir, no_wait):
    codes = codes_of("99:01:0101001", (11, 12, 13))

    batch_parser(codes, output="output", file_name="mylist", delay=2)
    assert no_wait == [0, 2, 2]

    del no_wait[:]
    batch_parser(codes, output="output", file_name="mylist", delay=2)
    assert no_wait == []


def test_repeat_asks_again_for_the_failed_ones(village, workdir):
    village.respond("99:01:0101001:12", 500, times=1)
    codes = codes_of("99:01:0101001", (11, 12, 13))

    results = batch_parser(codes, output="output", file_name="mylist", delay=0, repeat=1)

    assert statuses(results) == ["ok", "ok", "ok"]
    assert village.requested_codes.count("99:01:0101001:12") == 2
