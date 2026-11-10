class RequestException(Exception):
    pass


class TimeoutException(RequestException):
    pass


class HTTPBadRequestException(RequestException):
    pass


class HTTPErrorException(RequestException):
    """Any HTTP status that is not handled separately (usually 5xx - a problem on the server side)."""

    def __init__(self, message: str = "", code: int = 0):
        super().__init__(message)
        self.code = code


class HTTPForbiddenException(RequestException):
    """HTTP 403: the service refuses to talk to this client / IP address. Retrying does not help."""


class TooManyRequestsException(HTTPErrorException):
    """HTTP 429: slow down."""


class BadResponseException(RequestException):
    """The service answered with something that is not the expected JSON document."""
