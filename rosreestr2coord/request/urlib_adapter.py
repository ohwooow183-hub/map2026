import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Dict, Optional, Union
from urllib.request import Request

from .base_adapter import RequestAdapter
from .exceptions import (
    BadResponseException,
    HTTPBadRequestException,
    HTTPErrorException,
    HTTPForbiddenException,
    RequestException,
    TimeoutException,
    TooManyRequestsException,
)


def _read_body(response, limit: int = 500) -> str:
    try:
        return response.read().decode("utf-8", "ignore")[:limit]
    except Exception:
        return ""


class UrllibAdapter(RequestAdapter):
    def _make_request(
        self,
        url: str,
        proxy: Optional[str],
        timeout: int,
        headers: dict,
        method: str = "GET",
        body: Optional[Union[Dict, bytes]] = None,
    ) -> Optional[dict]:
        """
        Returns the decoded JSON document, or None when the service answers 404 (nothing found).
        Every other failure is raised as a RequestException subclass, so that "nothing found"
        can never be confused with "the request did not go through".
        """
        if body and isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")

        request = Request(url, data=body, headers=headers, method=method)

        # nspd.gov.ru uses a certificate issued by the Russian national CA,
        # which is not in the trust store of most systems
        context = ssl._create_unverified_context()

        try:
            if proxy:
                proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                https_handler = urllib.request.HTTPSHandler(context=context)
                opener = urllib.request.build_opener(proxy_handler, https_handler)

                response = opener.open(request, timeout=max(timeout, 30))
            else:
                response = urllib.request.urlopen(request, context=context, timeout=timeout)

            encoding = response.headers.get_content_charset() or "utf-8"
            data = response.read().decode(encoding, "replace")

        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 403:
                raise HTTPForbiddenException(f"HTTP {e.code}: {e.reason}; body={_read_body(e)}") from e
            if e.code == 400:
                raise HTTPBadRequestException("HTTP 400 Bad Request") from e
            if e.code == 429:
                raise TooManyRequestsException("HTTP 429 Too Many Requests", code=e.code) from e
            raise HTTPErrorException(f"HTTP {e.code}: {e.reason}", code=e.code) from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                raise TimeoutException("The request timed out") from e
            # refused / reset connection, TLS failure, unknown host, proxy failure ...
            raise RequestException(f"no connection to the service: {e.reason}") from e
        except (TimeoutError, socket.timeout) as e:
            raise TimeoutException("The request timed out") from e

        try:
            return json.loads(data)
        except ValueError as e:
            snippet = " ".join(data[:200].split())
            raise BadResponseException(f"Unexpected (non JSON) answer from the service: {snippet!r}") from e

    def get_specific_http_error(self):
        """Returns the urllib HTTPError class."""
        return urllib.error.HTTPError

    def is_specific_error(self, er: Exception) -> bool:
        """Checks if the HTTP error code is 400."""
        return hasattr(er, "code") and er.code == 400
