# coding: utf-8
import json
import logging
import os
import warnings
from typing import Dict, List, Optional, Union
from urllib.parse import quote

from .attributes import feature_number, feature_numbers
from .export import feature2kml
from .geometry import has_shape
from .logger import logger
from .request.exceptions import HTTPForbiddenException, RequestException, TimeoutException, TooManyRequestsException
from .request.proxy_handling import ProxyHandling
from .request.request import make_request
from .utils import clean_code, clear_code, code_to_filename, codes_equal, is_cadastral_number, transform_to_wgs

TYPES = {
    "Объекты недвижимости": 1,
    "Кадастровое деление": 2,
    "Административно-территориальное деление": 4,
    "Зоны и территории": 5,
    "Территориальные зоны": 7,
    "Комплексы объектов": 15,
}

# Search endpoint of the NSPD geoportal. Can be redirected (tests, a relay of your own) with the NSPD_SEARCH_URL
# environment variable.
DEFAULT_SEARCH_URL = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
SEARCH_URL_ENV = "NSPD_SEARCH_URL"

# What is known about the requested object after the request
STATUS_OK = "ok"  # found, boundaries received (an outline, or a line for linear objects)
STATUS_NO_BOUNDS = "no_bounds"  # found in the registry, but without boundaries (a point or no geometry at all)
STATUS_NOT_FOUND = "not_found"  # the service answered and has no such object
STATUS_ERROR = "error"  # the request failed - nothing is known about the object
STATUS_SKIPPED = "skipped"  # the request was not made (batch processing was interrupted)

STATUSES = (STATUS_OK, STATUS_NO_BOUNDS, STATUS_NOT_FOUND, STATUS_ERROR, STATUS_SKIPPED)


class NoCoordinatesException(Exception):
    """Exception raised when no coordinates are found."""

    pass


def describe_error(er: BaseException) -> str:
    """One line a user can act upon."""
    if isinstance(er, HTTPForbiddenException):
        return (
            "access denied by nspd.gov.ru (HTTP 403): the service refuses requests from this IP address. "
            "It reportedly accepts Russian IP addresses only and bans too frequent requests - "
            "try later or use a proxy (--proxy_url)"
        )
    if isinstance(er, TooManyRequestsException):
        return "too many requests (HTTP 429). Increase the delay between requests (--delay) and try later"
    if isinstance(er, TimeoutException):
        return "no answer from nspd.gov.ru (%s). Increase --timeout, try later or use a proxy (--proxy_url)" % er
    message = str(getattr(er, "reason", "") or er) or er.__class__.__name__
    return message


def feature_status(feature: Optional[dict]) -> str:
    """Status of an object by the feature received for it."""
    if not feature:
        return STATUS_NOT_FOUND
    return STATUS_OK if has_shape(feature.get("geometry")) else STATUS_NO_BOUNDS


class Area:
    def __init__(
        self,
        code: str = "",
        area_type: Optional[int] = 1,
        media_path: str = "",
        with_log: bool = True,
        coord_out: str = "EPSG:4326",
        with_proxy: bool = False,
        use_cache: bool = True,
        proxy_handler: Optional[ProxyHandling] = None,
        timeout: int = 5,
        logger: Optional[logging.Logger] = logger,
        proxy_url: Optional[str] = None,
    ):
        self.code: str = clean_code(code or "")
        self.area_type: Optional[int] = area_type
        self.media_path: str = media_path or os.getcwd()
        self.with_log: bool = with_log
        self.coord_out: str = coord_out
        self.with_proxy: bool = with_proxy
        self.use_cache: bool = use_cache
        self.timeout: int = timeout
        self.proxy_handler: Optional[ProxyHandling] = proxy_handler
        self.proxy_url: Optional[str] = proxy_url
        self.logger: logging.Logger = logger or logging.getLogger(__name__)

        self.file_name: str = code_to_filename(self.code)
        self.feature: Optional[dict] = None
        # One of STATUS_*; None until a request is made
        self.status: Optional[str] = None
        # The exception that interrupted the request (status == "error")
        self.exception: Optional[BaseException] = None
        # Numbers of the objects that the search returned instead of the requested one
        self.candidates: List[str] = []

        self.tmp_path: str = self.create_tmp()

        if not self.code:
            return

        if with_log:
            try:
                geom = self.get_geometry()
                if not geom:
                    self.log("Nothing found")
            except Exception as er:
                # the error is reported, not raised: keep it for the caller
                self.status = STATUS_ERROR
                self.exception = self.exception or er
                self.log(describe_error(er))
        else:
            geom = self.get_geometry()

    def create_tmp(self) -> str:
        tmp_path = os.path.join(self.media_path, "tmp")
        os.makedirs(tmp_path, exist_ok=True)
        return tmp_path

    def _build_url(self, area_type: int) -> str:
        base_url = os.environ.get(SEARCH_URL_ENV) or DEFAULT_SEARCH_URL
        params = [
            f"thematicSearchId={area_type}",
            # numbers go as they are, anything else (spaces, cyrillic letters) must be percent-encoded
            f"query={quote(self.code, safe=':')}",
            f"CRS={self.coord_out}",
        ]
        return f"{base_url}?{'&'.join(params)}"

    def _is_requested(self, feature: dict) -> bool:
        return any(codes_equal(self.code, number) for number in feature_numbers(feature))

    def _select_feature(self, features: List[dict]) -> Optional[dict]:
        """
        Choose the requested object among the search results.

        The search is a full-text one: besides the object with the requested number it may return other objects
        that mention this number (for example premises of a building) or objects with a similar number.
        Taking simply the first result would silently put a different object on the map under the requested number.
        """
        features = [f for f in features if isinstance(f, dict) and self._matches_criteria(f)]
        if not features:
            return None

        exact = [f for f in features if self._is_requested(f)]
        if exact:
            # if the object is listed twice, prefer the record that has boundaries
            exact.sort(key=lambda f: 0 if has_shape(f.get("geometry")) else 1)
            return exact[0]

        self.candidates = [feature_number(f) or "?" for f in features]
        if is_cadastral_number(self.code):
            # a full cadastral number identifies one object - never substitute another one for it
            self.logger.warning(
                "The search returned only other objects for %s: %s" % (self.code, ", ".join(self.candidates[:10]))
            )
            return None
        # free-form queries (districts, zones, ...): keep the first result as before
        return features[0]

    def _query_with_area_type(self, area_type: int) -> Optional[dict]:
        url = self._build_url(area_type)
        self.feature = None
        self.exception = None
        self.candidates = []
        try:
            resp = self.make_request(url)
        except Exception as er:
            self.status = STATUS_ERROR
            self.exception = er
            raise

        features = []
        if isinstance(resp, dict):
            data = resp.get("data")
            if isinstance(data, dict):
                features = data.get("features") or []
            elif isinstance(resp.get("features"), list):
                features = resp["features"]

        feature = self._select_feature(features)
        if feature is None:
            self.status = STATUS_NOT_FOUND
            return None

        feature = transform_to_wgs(feature)
        self.feature = feature
        self.status = feature_status(feature)
        return feature

    def get_geometry(self) -> Optional[dict]:
        if self.area_type is not None:
            if self.area_type not in TYPES.values():
                raise ValueError(
                    "Invalid area_type specified. Available options: "
                    + ", ".join([f"{k} - {v}" for k, v in TYPES.items()])
                )
            return self._query_with_area_type(self.area_type)

    def _matches_criteria(self, feature: dict) -> bool:
        return True

    @property
    def has_bounds(self) -> bool:
        """True if boundaries of the object are known (an outline, or a line for linear objects)."""
        return bool(self.feature) and has_shape(self.feature.get("geometry"))

    @property
    def error_message(self) -> str:
        """Why the request failed (an empty string if it did not)."""
        return describe_error(self.exception) if self.exception else ""

    def to_geojson_poly(self, dumps: bool = True) -> Union[str, dict, None]:
        warnings.warn("to_geojson_poly is deprecated. Use to_geojson instead.", DeprecationWarning)
        return self.to_geojson(dumps=dumps)

    def to_geojson(self, dumps: bool = True) -> Union[str, dict, None]:
        if self.feature:
            return json.dumps(self.feature, ensure_ascii=False) if dumps else self.feature
        return None

    def to_kml(self):
        if not self.feature:
            raise NoCoordinatesException("No geometry feature available for conversion to KML.")
        return feature2kml(self.feature)

    def _make_request(
        self,
        url: str,
        method: str,
        body: Optional[Union[Dict, bytes]] = None,
    ) -> dict:
        proxy_path = os.path.join(self.tmp_path, "proxy.txt")
        effective_proxy_handler = self.proxy_handler or ProxyHandling(path=proxy_path)
        self.logger.debug(f"Request URL: {url}")
        headers = {"Content-Type": "application/json"}
        response = make_request(
            url=url,
            body=body,
            method=method,
            with_proxy=self.with_proxy,
            proxy_handler=effective_proxy_handler,
            logger=self.logger,
            timeout=self.timeout,
            headers=headers,
            proxy_url=self.proxy_url,
        )
        return response

    def make_request(
        self,
        url: str,
        method: str = "GET",
        body: Optional[Union[Dict, bytes]] = None,
    ) -> dict:
        return self._make_request(url, method, body)

    @staticmethod
    def clear_code(code: str) -> str:
        return clear_code(code)

    def log(self, msg: str) -> None:
        if self.with_log:
            self.logger.info(msg)
            print(msg)

    def error(self, msg: str) -> None:
        if self.with_log:
            self.logger.warning(msg)


__all__ = [
    "Area",
    "NoCoordinatesException",
    "RequestException",
    "STATUSES",
    "STATUS_ERROR",
    "STATUS_NOT_FOUND",
    "STATUS_NO_BOUNDS",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "TYPES",
    "describe_error",
    "feature_status",
]
