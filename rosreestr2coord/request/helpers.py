import json
from typing import Any, Dict, Union

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

REFERER = "https://nspd.gov.ru/map?thematic=PKK"


def error_code(response: Any) -> Union[int, None]:
    """Numeric code of an NSPD error document (`{"code": 403, "message": "Forbidden", "requestId": "..."}`)."""
    if isinstance(response, dict) and "data" not in response and "features" not in response:
        try:
            return int(response.get("code"))
        except (TypeError, ValueError):
            return None
    return None


def is_error_response(url: str, response: Any) -> Union[bool, str]:
    """
    Returns an error message if the answer of the service describes an error, otherwise False.
    Accepts both a raw body and an already decoded JSON document.
    """
    data = response
    if isinstance(response, (bytes, str)):
        try:
            data = json.loads(response)
        except Exception:
            return False
    if not isinstance(data, dict):
        return False

    error = data.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        return message if message else "error"

    code = error_code(data)
    if code is not None and code != 404 and code >= 400:
        return "%s (code %s)" % (data.get("message") or "error", code)
    return False


def get_rosreestr_headers() -> Dict[str, str]:
    return {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,ru-RU;q=0.8,ru;q=0.7,es;q=0.6",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": REFERER,
        "origin": "https://nspd.gov.ru",
        "user-agent": USER_AGENT,
    }
