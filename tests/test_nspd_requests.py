"""Requests to the service, checked through real HTTP against the local stand-in (tests/nspd_mock.py)."""

import pytest
from rosreestr2coord.parser import (
    STATUS_ERROR,
    STATUS_NO_BOUNDS,
    STATUS_NOT_FOUND,
    STATUS_OK,
    Area,
    describe_error,
)
from rosreestr2coord.request.exceptions import (
    BadResponseException,
    HTTPErrorException,
    HTTPForbiddenException,
    RequestException,
    TimeoutException,
    TooManyRequestsException,
)

from .nspd_mock import FORBIDDEN_PAGE, MockNspd, make_feature

pytestmark = pytest.mark.unit


def test_found_object_comes_in_wgs84(nspd, sample):
    nspd.add(sample)

    area = Area("50:33:0000000:98325", with_log=False)

    assert area.status == STATUS_OK and area.has_bounds
    geometry = area.to_geojson(dumps=False)["geometry"]
    assert geometry["type"] == "MultiPolygon" and len(geometry["coordinates"]) == 3
    lon, lat = geometry["coordinates"][0][0][0]
    assert 37.9 < lon < 38.2 and 55.1 < lat < 55.4
    assert "crs" not in geometry
    assert area.to_kml()


def test_request_is_the_one_the_service_expects(nspd, sample):
    nspd.add(sample)

    Area("50:33:0000000:98325", with_log=False)

    (request,) = nspd.requests
    assert request["path"] == "/api/geoportal/v2/search/geoportal"
    assert request["query"]["thematicSearchId"] == ["1"]
    assert request["query"]["query"] == ["50:33:0000000:98325"]
    # the service answers 403 without this header
    assert request["headers"]["referer"] == "https://nspd.gov.ru/map?thematic=PKK"
    assert "Mozilla" in request["headers"]["user-agent"]


def test_area_type_goes_to_the_request(nspd):
    nspd.add(make_feature("75:09:300418"))

    area = Area("75:09:300418", area_type=2, with_log=False)

    assert nspd.requests[0]["query"]["thematicSearchId"] == ["2"]
    assert area.status == STATUS_OK


def test_nothing_found_is_not_an_error(nspd):
    area = Area("99:01:0101001:15", with_log=False)

    assert area.status == STATUS_NOT_FOUND
    assert area.feature is None and area.exception is None
    assert area.to_geojson() is None


def test_object_without_boundaries(nspd):
    nspd.add(make_feature("99:01:0101001:13", geometry="Point"))
    nspd.add(make_feature("99:01:0101001:14", geometry=None))

    point = Area("99:01:0101001:13", with_log=False)
    nothing = Area("99:01:0101001:14", with_log=False)

    assert point.status == STATUS_NO_BOUNDS and not point.has_bounds
    assert point.feature["properties"]["label"] == "99:01:0101001:13"
    assert nothing.status == STATUS_NO_BOUNDS and nothing.feature is not None


def test_the_requested_object_is_chosen_among_the_search_results(nspd):
    # the search is a full-text one: an object that merely mentions the number may come first
    nspd.add(make_feature("99:01:0101001:290", x=4239000.0), code="99:01:0101001:29")
    nspd.add(make_feature("99:01:0101001:29", x=4233000.0), code="99:01:0101001:29")

    area = Area("99:01:0101001:29", with_log=False)

    assert area.status == STATUS_OK
    assert area.feature["properties"]["label"] == "99:01:0101001:29"


def test_another_object_is_never_substituted_for_the_requested_number(nspd):
    nspd.add(make_feature("99:01:0101001:290"), code="99:01:0101001:29")

    area = Area("99:01:0101001:29", with_log=False)

    assert area.status == STATUS_NOT_FOUND
    assert area.feature is None
    assert area.candidates == ["99:01:0101001:290"]


def test_number_written_without_leading_zeros_is_recognised(nspd):
    nspd.add(make_feature("38:06:144003:4723"), code="38:6:144003:4723")

    area = Area("38:6:144003:4723", with_log=False)

    assert area.status == STATUS_OK


@pytest.mark.parametrize(
    "status,body,content_type,expected",
    [
        (403, FORBIDDEN_PAGE, "text/html", HTTPForbiddenException),
        (403, {"code": 403, "message": "Forbidden"}, "application/json", HTTPForbiddenException),
        (429, None, "application/json", TooManyRequestsException),
        (500, None, "application/json", HTTPErrorException),
        (502, "<html>Bad Gateway</html>", "text/html", HTTPErrorException),
        (200, "<html>maintenance</html>", "text/html", BadResponseException),
        (200, {"code": 403, "message": "Forbidden"}, "application/json", HTTPForbiddenException),
    ],
)
def test_failed_request_is_an_error_not_an_empty_result(nspd, status, body, content_type, expected):
    nspd.add(make_feature("99:01:0101001:11"))
    nspd.respond("99:01:0101001:11", status, body=body, content_type=content_type)

    with pytest.raises(expected) as raised:
        Area("99:01:0101001:11", with_log=False)
    assert isinstance(raised.value, RequestException)


def test_error_is_reported_instead_of_raised_with_log(nspd, capsys):
    nspd.respond("99:01:0101001:11", 403, body=FORBIDDEN_PAGE, content_type="text/html")

    area = Area("99:01:0101001:11", with_log=True)

    assert area.status == STATUS_ERROR
    assert isinstance(area.exception, HTTPForbiddenException)
    assert "403" in area.error_message and "--proxy_url" in area.error_message
    assert "403" in capsys.readouterr().out


def test_missing_referer_is_refused_by_the_stand_in(workdir, monkeypatch):
    """The stand-in really checks what the package sends."""
    monkeypatch.setattr("rosreestr2coord.request.base_adapter.get_rosreestr_headers", lambda: {"accept": "*/*"})
    with MockNspd() as mock:
        mock.add(make_feature("99:01:0101001:11"))
        monkeypatch.setenv("NSPD_SEARCH_URL", mock.url)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        with pytest.raises(HTTPForbiddenException):
            Area("99:01:0101001:11", with_log=False)


def test_unreachable_service_is_an_error(workdir, monkeypatch):
    # nothing listens on this port
    monkeypatch.setenv("NSPD_SEARCH_URL", "http://127.0.0.1:9/api/geoportal/v2/search/geoportal")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    with pytest.raises(RequestException) as raised:
        Area("99:01:0101001:11", with_log=False, timeout=2)

    assert "no connection" in str(raised.value)


def test_errors_are_described_for_the_user():
    assert "--proxy_url" in describe_error(HTTPForbiddenException("HTTP 403"))
    assert "--delay" in describe_error(TooManyRequestsException("HTTP 429", code=429))
    assert "--timeout" in describe_error(TimeoutException("The request timed out"))
    assert describe_error(HTTPErrorException("HTTP 502: Bad Gateway", code=502)) == "HTTP 502: Bad Gateway"
    assert describe_error(ValueError()) == "ValueError"


def test_free_form_query_is_percent_encoded(nspd):
    nspd.add(make_feature("Иркутская область"), code="Иркутская область")

    area = Area("Иркутская область", area_type=4, with_log=False)

    assert nspd.requests[0]["query"]["query"] == ["Иркутская область"]
    assert area.status == STATUS_OK
