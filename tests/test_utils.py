import copy

import pytest
from rosreestr2coord.utils import (
    clean_code,
    clear_code,
    code_to_filename,
    codes_equal,
    is_cadastral_number,
    is_projected,
    lonlat2xy,
    transform_to_wgs,
    xy2lonlat,
)

from .nspd_mock import make_feature

pytestmark = pytest.mark.unit


def test_mercator_round_trip():
    lon, lat = 38.0, 55.2
    x, y = lonlat2xy(lon, lat)
    assert abs(x) > 1e6 and abs(y) > 1e6
    back = xy2lonlat(x, y)
    assert back == pytest.approx([lon, lat], abs=1e-9)


def test_transform_converts_every_part_of_a_multipolygon(sample):
    assert sample["geometry"]["type"] == "MultiPolygon"
    assert is_projected(sample["geometry"])

    result = transform_to_wgs(sample)

    parts = result["geometry"]["coordinates"]
    assert len(parts) == len(sample["geometry"]["coordinates"]) == 3
    for polygon in parts:
        for ring in polygon:
            for lon, lat in ring:
                # the plot is near Stupino, Moscow Oblast
                assert 37.9 < lon < 38.2 and 55.1 < lat < 55.4


def test_transform_does_not_touch_the_input(sample):
    before = copy.deepcopy(sample)
    transform_to_wgs(sample)
    assert sample == before


def test_transform_drops_the_stale_crs(sample):
    assert sample["geometry"]["crs"]["properties"]["name"] == "EPSG:3857"
    assert "crs" not in transform_to_wgs(sample)["geometry"]


def test_transform_is_safe_to_apply_twice(sample):
    once = transform_to_wgs(sample)
    assert transform_to_wgs(once) == once


def test_transform_converts_points():
    feature = make_feature("99:01:0101001:13", geometry="Point", x=4233000.0, y=7405000.0)
    lon, lat = transform_to_wgs(feature)["geometry"]["coordinates"]
    assert [lon, lat] == pytest.approx(xy2lonlat(4233000.0, 7405000.0))
    assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_transform_accepts_a_feature_without_geometry():
    feature = make_feature("99:01:0101001:14", geometry=None)
    assert transform_to_wgs(feature)["geometry"] is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("38:06:144003:4723\n", "38:06:144003:4723"),
        ("\ufeff38:06:144003:4723\r\n", "38:06:144003:4723"),
        ("  «38:06:144003:4723»;", "38:06:144003:4723"),
        ("'38:06:144003:4723',", "38:06:144003:4723"),
        ("\u00a038:06:144003:4723\u00a0", "38:06:144003:4723"),
        ("", ""),
        ("  \n", ""),
    ],
)
def test_clean_code(raw, expected):
    assert clean_code(raw) == expected


def test_codes_equal_ignores_leading_zeros_only():
    assert codes_equal("38:06:144003:4723", "38:6:144003:4723")
    assert codes_equal(" 38:06:144003:4723 ", "38:06:144003:4723")
    assert not codes_equal("38:06:144003:4723", "38:06:144003:47230")
    assert not codes_equal("38:06:144003:4723", "38:06:144003:472")
    assert not codes_equal("38:06:144003:4723", None)
    assert not codes_equal("", "")


def test_clear_code_keeps_zone_numbers():
    assert clear_code("38:06:000000:1006") == "38:6:0:1006"
    assert clear_code("02:02-6.667") == "02:02-6.667"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("38:06:144003:4723", True),
        ("50:33:0000000:98325", True),
        ("77:01:0001001:1001/5", True),
        ("38:06:144003", False),
        ("75:09-4.1", False),
        ("Иркутск", False),
    ],
)
def test_is_cadastral_number(code, expected):
    assert is_cadastral_number(code) is expected


def test_code_to_filename():
    assert code_to_filename("38:06:144003:4723") == "38_06_144003_4723"
    assert code_to_filename("77:01:0001001:1001/5") == "77_01_0001001_1001-5"
