"""
A local stand-in for the search endpoint of nspd.gov.ru.

The tests must not depend on the real service (it is rate limited, blocks foreign IP addresses and its data
changes), but they should still go through real HTTP. The mock repeats what is known about the behaviour
of the service:

* `GET /api/geoportal/v2/search/geoportal?thematicSearchId=1&query=<number>`;
* no `Referer` header - `403` with an HTML page of the firewall;
* nothing found - `404`;
* found - `{"data": {"type": "FeatureCollection", "features": [...]}, "meta": [...]}` with EPSG:3857 geometry.
"""

import copy
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SEARCH_PATH = "/api/geoportal/v2/search/geoportal"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

FORBIDDEN_PAGE = (
    "<html><head><title>Forbidden</title></head><body><h1>Forbidden</h1>"
    "<p>Request ID: 00000000-0000-0000-0000-000000000000</p><p>Client IP: 203.0.113.7</p><p>Rule id: 1</p>"
    "</body></html>"
)


def load_sample():
    """A real answer of the service for the multi-contour land plot 50:33:0000000:98325 (Moscow Oblast)."""
    with open(os.path.join(DATA_DIR, "nspd_search_50_33_0000000_98325.json"), encoding="utf-8") as f:
        return json.load(f)


def sample_feature():
    return load_sample()["data"]["features"][0]


def make_feature(code, x=4233000.0, y=7405000.0, width=60.0, height=40.0, geometry="Polygon", **options):
    """A made up object in the format of the service. Coordinates are EPSG:3857 metres."""
    ring = [[x, y], [x + width, y], [x + width, y + height], [x, y + height], [x, y]]
    if geometry == "Polygon":
        geom = {"type": "Polygon", "coordinates": [ring]}
    elif geometry == "MultiPolygon":
        shifted = [[px + width * 1.5, py] for px, py in ring]
        geom = {"type": "MultiPolygon", "coordinates": [[ring], [shifted]]}
    elif geometry == "Point":
        geom = {"type": "Point", "coordinates": [x, y]}
    elif geometry is None:
        geom = None
    else:
        raise ValueError(geometry)
    if geom is not None:
        geom["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}

    quarter = code.rsplit(":", 1)[0] if ":" in code else ""
    feature_options = {
        "cad_num": code,
        "quarter_cad_number": quarter,
        "land_record_type": "Земельный участок",
        "readable_address": "Тестовая область, тестовый район, д. Тестовая",
        "specified_area": 1000,
        "status": "Учтенный",
        "cost_value": 0,
    }
    feature_options.update(options)
    return {
        "id": abs(hash(code)) % 10**9,
        "type": "Feature",
        "geometry": geom,
        "properties": {
            "category": 36368,
            "categoryName": "Земельные участки ЕГРН",
            "descr": code,
            "externalKey": code,
            "label": code,
            "options": feature_options,
        },
    }


class MockNspd:
    """
    with MockNspd() as nspd:
        nspd.add(feature)                 # the object is returned for its own number
        nspd.respond("77:01:1:1", 403)    # always answer with this HTTP status
        nspd.respond("77:01:1:2", 429, times=1)  # ... only once, then answer normally
        os.environ["NSPD_SEARCH_URL"] = nspd.url
    """

    def __init__(self, require_referer=True):
        self.require_referer = require_referer
        self.objects = {}
        self.rules = {}
        self.requests = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    # --- configuration ---

    def add(self, feature, code=None):
        """Return `feature` for the query `code` (its own number by default). Several features may be added."""
        code = code or feature["properties"]["label"]
        self.objects.setdefault(code, []).append(feature)
        return self

    def respond(self, code, status, times=None, body=None, content_type="application/json"):
        """Answer the query `code` with the HTTP status (and body) `times` times (always if None)."""
        self.rules.setdefault(code, []).append(
            {"status": status, "times": times, "body": body, "content_type": content_type}
        )
        return self

    # --- lifecycle ---

    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, body, content_type="application/json"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body, ensure_ascii=False)
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urlparse(self.path)
                query = parse_qs(url.query)
                code = (query.get("query") or [""])[0]
                with mock._lock:
                    mock.requests.append(
                        {
                            "time": time.time(),
                            "path": url.path,
                            "code": code,
                            "query": query,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                        }
                    )
                    rule = None
                    for candidate in mock.rules.get(code, []):
                        if candidate["times"] is None or candidate["times"] > 0:
                            if candidate["times"] is not None:
                                candidate["times"] -= 1
                            rule = candidate
                            break

                if url.path != SEARCH_PATH:
                    return self._send(404, {"code": 404, "message": "Not Found"})
                if mock.require_referer and "nspd.gov.ru" not in self.headers.get("Referer", ""):
                    return self._send(403, FORBIDDEN_PAGE, "text/html")
                if rule:
                    body = rule["body"]
                    if body is None:
                        body = {"code": rule["status"], "message": "Error"}
                    return self._send(rule["status"], body, rule["content_type"])

                features = copy.deepcopy(mock.objects.get(code) or [])
                if not features:
                    return self._send(404, {"code": 404, "message": "Not Found"})
                return self._send(
                    200,
                    {
                        "data": {"type": "FeatureCollection", "features": features},
                        "meta": [{"totalCount": len(features), "categoryId": 36368}],
                    },
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        # a short poll interval: `shutdown()` waits for one
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # --- inspection ---

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return "http://%s:%s%s" % (host, port, SEARCH_PATH)

    @property
    def requested_codes(self):
        return [r["code"] for r in self.requests]
