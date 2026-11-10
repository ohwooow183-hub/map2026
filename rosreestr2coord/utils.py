import copy
import math
import re
from typing import Any, List, Optional


def y2lat(y: float) -> float:
    return (2 * math.atan(math.exp(y / 6378137)) - math.pi / 2) / (math.pi / 180)


def x2lon(x: float) -> float:
    return x / (math.pi / 180.0) / 6378137.0


def xy2lonlat(x: float, y: float) -> List[float]:
    return [x2lon(x), y2lat(y)]


def lonlat2xy(lon: float, lat: float) -> List[float]:
    """WGS84 lon/lat -> EPSG:3857 (the inverse of `xy2lonlat`)."""
    x = math.radians(lon) * 6378137.0
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0
    return [x, y]


def code_to_filename(code: str) -> str:
    return code.replace(":", "_").replace("/", "-")


def _has_projected_magnitude(coords: Any) -> bool:
    """True if any position cannot be lon/lat degrees (|x| > 180 or |y| > 90)."""
    if isinstance(coords, (list, tuple)) and coords:
        if isinstance(coords[0], (int, float)):
            return len(coords) >= 2 and (abs(coords[0]) > 180 or abs(coords[1]) > 90)
        return any(_has_projected_magnitude(c) for c in coords)
    return False


def is_projected(geometry: dict) -> bool:
    """
    Tell whether geometry coordinates are Web Mercator metres rather than WGS84 degrees.

    NSPD announces the CRS in the (non standard) `geometry.crs` member, but the decision is made on the
    magnitude of the numbers: anywhere in Russia the EPSG:3857 values are in the millions, so metres and
    degrees can not be confused. This keeps the conversion correct even if the service starts honouring
    a `CRS` request parameter, and makes the conversion safe to apply twice.
    """
    return _has_projected_magnitude(geometry.get("coordinates"))


def _convert_coords(coords: Any) -> Any:
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        return xy2lonlat(coords[0], coords[1]) + list(coords[2:])
    if isinstance(coords, (list, tuple)):
        return [_convert_coords(c) for c in coords]
    return coords


def geometry_to_wgs(geometry: Optional[dict]) -> Optional[dict]:
    """Return a copy of a GeoJSON geometry of any type with coordinates in WGS84 lon/lat."""
    if not isinstance(geometry, dict):
        return geometry
    result = copy.deepcopy(geometry)
    if result.get("type") == "GeometryCollection":
        result["geometries"] = [geometry_to_wgs(g) for g in result.get("geometries") or []]
        result.pop("crs", None)
        return result
    if is_projected(result):
        result["coordinates"] = _convert_coords(result.get("coordinates"))
    # After the conversion the announced "EPSG:3857" would be wrong; RFC 7946 GeoJSON is WGS84 by definition.
    result.pop("crs", None)
    return result


def transform_to_wgs(geojson: dict) -> dict:
    """
    Convert a GeoJSON Feature returned by NSPD (EPSG:3857) to WGS84.

    Works for every geometry type (objects without surveyed boundaries come as `Point`),
    never touches the input object and is safe to call twice.
    """
    result = copy.deepcopy(geojson)
    if isinstance(result, dict) and isinstance(result.get("geometry"), dict):
        result["geometry"] = geometry_to_wgs(result["geometry"])
    return result


_CODE_JUNK = r"[\s'\"«»,;\u200b\ufeff]+"


def clean_code(code: str) -> str:
    """Strip whitespace (incl. non-breaking), quotes, BOM and separators that come with copy-pasted lists."""
    return re.sub(r"^%s|%s$" % (_CODE_JUNK, _CODE_JUNK), "", str(code))


def clear_code(code: str) -> str:
    """
    Remove first nulls from code xxxx:00xx >> xxxx:xx
    but if the cadastral number, for example "02:02-6.667",
    then the all parts will remain zeros
    """
    is_delimited_code = re.match(r"^\d+(\:\d+)", code)
    leave_zeros = "." in code
    if is_delimited_code and not leave_zeros:
        parts = []
        for x in code.split(":"):
            strip_zeros = x.lstrip("0")
            if strip_zeros:
                parts.append(strip_zeros)
            else:
                parts.append("0")
        return ":".join(parts)
    return code


def codes_equal(a: Any, b: Any) -> bool:
    """Compare two cadastral numbers ignoring surrounding whitespace and leading zeros (38:06:... == 38:6:...)."""
    if a is None or b is None:
        return False
    a, b = clean_code(str(a)), clean_code(str(b))
    if not a or not b:
        return False
    return a == b or clear_code(a) == clear_code(b)


def is_cadastral_number(code: str) -> bool:
    """`AA:BB:CCCCCCC:N` - the number of a real estate object (as opposed to quarters, zones, ...)."""
    return bool(re.match(r"^\d+:\d+:\d+:\d+(/\d+)?$", clean_code(code)))
