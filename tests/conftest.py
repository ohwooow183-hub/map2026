from unittest.mock import patch

import pytest
from rosreestr2coord.parser import Area


@pytest.fixture
def feature_from_api():
    return {
        "id": 39128231,
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [104.63460275151176, 52.26667215769885],
                    [104.63450479864538, 52.266502806027226],
                    [104.63519493087094, 52.266352425260756],
                    [104.63529288601124, 52.266521776345144],
                    [104.63460275151176, 52.26667215769885],
                ]
            ],
            "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        },
        "properties": {"label": "38:06:144003:4723", "options": {"cad_num": "38:06:144003:4723", "cost_value": 271540}},
    }


@pytest.fixture
def mock_area(feature_from_api):
    with patch("rosreestr2coord.parser.Area.get_geometry"):
        area = Area("38:06:144003:4723", area_type=1)
        area.feature = feature_from_api
        return area


# --- a local stand-in for nspd.gov.ru (see tests/nspd_mock.py) ---


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Run the test in an empty directory: the package creates `tmp/` and `output/` in the current one."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def nspd(workdir, monkeypatch):
    """A local HTTP server that answers like the NSPD search; the package is pointed to it."""
    from .nspd_mock import MockNspd

    # the mock listens on 127.0.0.1: a proxy configured in the environment must not get in the way
    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    with MockNspd() as mock:
        monkeypatch.setenv("NSPD_SEARCH_URL", mock.url)
        yield mock


@pytest.fixture
def no_wait(monkeypatch):
    """Do not really wait between the requests of a batch. Returns the list of the requested pauses."""
    pauses = []
    monkeypatch.setattr("rosreestr2coord.batch.sleep", lambda seconds: pauses.append(seconds))
    return pauses


@pytest.fixture
def sample():
    """A real object as the service returns it: the multi-contour land plot 50:33:0000000:98325 (EPSG:3857)."""
    from .nspd_mock import sample_feature

    return sample_feature()
