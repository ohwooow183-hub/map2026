import json
import os
import re
import time
from time import sleep

from .attributes import feature_numbers
from .export import area_json_output, batch_csv_output, batch_json_output, csv_row, make_output
from .parser import (
    STATUS_ERROR,
    STATUS_NO_BOUNDS,
    STATUS_NOT_FOUND,
    STATUS_OK,
    STATUS_SKIPPED,
    Area,
    describe_error,
    feature_status,
)
from .request.exceptions import HTTPForbiddenException, TooManyRequestsException
from .utils import clean_code, code_to_filename, codes_equal, transform_to_wgs

# Stop the batch after this number of requests failed in a row: the service is unreachable or blocks us,
# and hammering it further only makes the block longer.
MAX_FAILURES_IN_A_ROW = 3
# Seconds to wait after "429 Too Many Requests" before the request is repeated
BACKOFF_429 = (15, 60)

STATUS_TITLES = {
    STATUS_OK: "ok",
    STATUS_NO_BOUNDS: "no boundaries",
    STATUS_NOT_FOUND: "not found",
    STATUS_ERROR: "error",
    STATUS_SKIPPED: "not requested",
}


def make_result(code, status, feature=None, message="", cached=False, retrieved=None):
    """What is known about one requested code."""
    return {
        "code": code,
        "status": status,
        "feature": feature,
        "message": message,
        "cached": cached,
        # unix time when the data was received from the service
        "retrieved": retrieved,
    }


# 38:06:144003:4723, 75:09:300418, 75:09-4.1 ... - as opposed to a free-form search query
_NUMBER_LIKE = re.compile(r"^\d+:\d+[\d:./-]*$")


def split_codes(line):
    """
    Codes of one line of a list. Usually it is a single code, but numbers copied from a letter or a table
    often come several on a line, separated by commas, semicolons or spaces.
    """
    tokens = [clean_code(token) for token in re.split(r"[\s,;]+", line)]
    tokens = [token for token in tokens if token]
    if len(tokens) > 1 and all(_NUMBER_LIKE.match(token) for token in tokens):
        return tokens
    # anything else is one search query, spaces included
    code = clean_code(line)
    return [code] if code else []


def read_codes(lines):
    """
    Codes from the lines of a list file: empty lines and `#` comments are skipped, quotes and separators
    left from copy-paste are removed, duplicates are dropped (the order is kept).
    """
    codes = []
    for line in lines:
        for code in split_codes(str(line).split("#", 1)[0]):
            if code not in codes:
                codes.append(code)
    return codes


def cache_path(output, code):
    return os.path.join(os.path.abspath(output), "geojson", "%s.geojson" % code_to_filename(code))


def load_cached(output, code):
    """
    The feature saved by a previous run for this code, or None.
    Returns (feature, time of the file).
    """
    path = cache_path(output, code)
    try:
        with open(path, "r", encoding="utf-8") as f:
            feature = json.load(f)
        mtime = os.path.getmtime(path)
    except (OSError, ValueError):
        return None, None
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        return None, None
    # the file must describe exactly the requested object
    if not any(codes_equal(code, number) for number in feature_numbers(feature)):
        return None, None
    return transform_to_wgs(feature), mtime


def _request_area(code, with_log, **kwargs):
    """
    Request one code. Waits and repeats the request if the service asks to slow down.
    Returns an Area or raises the error of the request (whatever `with_log` is).
    """
    pauses = list(BACKOFF_429)
    while True:
        area = None
        try:
            area = Area(code, with_log=with_log, **kwargs)
            # with_log=True: the Area reports the error itself and does not raise
            error = area.exception
        except Exception as er:
            error = er
        if isinstance(error, TooManyRequestsException) and pauses:
            pause = pauses.pop(0)
            print(" - too many requests, waiting %i s" % pause, end="", flush=True)
            sleep(pause)
            continue
        if error:
            raise error
        return area


def batch_parser(
    codes, with_log=False, file_name="example", areas=None, output=os.path.join("output"), repeat=0, delay=1, **kwargs
):
    """
    Request every code of the list and write the results:
    a geojson per object, one geojson with all objects and a csv report with the status of every code.

    Returns a list of results (see `make_result`) in the order of the list - one per code,
    whether it was found or not.
    """
    codes = read_codes(codes)
    if areas is None:
        areas = []
    # the cadastral number identifies a real estate object uniquely - only then a saved file can be trusted
    use_cache = bool(kwargs.get("use_cache", True)) and kwargs.get("area_type", 1) == 1

    print("================================")
    print("Launched parsing of %i areas:" % len(codes))
    print("================================")

    results = {}
    pending = list(codes)
    stop_reason = ""
    for attempt in range(int(repeat or 0) + 1):
        if attempt:
            print("Retries parse areas with error")
        stop_reason = _run(pending, results, areas, with_log, output, delay, use_cache, kwargs)
        pending = [c for c in codes if results[c]["status"] in (STATUS_ERROR, STATUS_SKIPPED)]
        if not pending or stop_reason:
            break

    ordered = [results[c] for c in codes]
    counts = {status: sum(1 for r in ordered if r["status"] == status) for status in STATUS_TITLES}

    print("=================")
    print("Parsing complete:")
    print("  success     : %i" % counts[STATUS_OK])
    print("  error       : %i" % counts[STATUS_ERROR])
    print("  no_coord    : %i" % counts[STATUS_NO_BOUNDS])
    print("  not found   : %i" % counts[STATUS_NOT_FOUND])
    if counts[STATUS_SKIPPED]:
        print("  not requested: %i" % counts[STATUS_SKIPPED])
    print("-----------------")
    if stop_reason:
        print("Stopped: %s" % stop_reason)
        print("Run the same command again later: the objects that are already saved will not be requested again.")
        print("-----------------")

    rows = [csv_row(r["code"], r["feature"], r["status"], r["message"]) for r in ordered]
    path = batch_csv_output(output, rows, file_name)
    print("Create report complete: %s" % path)

    features = [r["feature"] for r in ordered if r["feature"]]
    if features:
        path = batch_json_output(output, features, file_name)
        print("Create output complete: %s" % path)

    problems = [r for r in ordered if r["status"] != STATUS_OK]
    if problems:
        print("-----------------")
        print("Without boundaries:")
        for r in problems:
            line = "%s - %s" % (r["code"], STATUS_TITLES[r["status"]])
            if r["message"] and r["status"] != STATUS_SKIPPED:
                line += ": %s" % r["message"]
            print(line)
    return ordered


def _run(codes, results, areas, with_log, output, delay, use_cache, kwargs):
    """Process the codes, fill `results`. Returns the reason if the processing had to be stopped."""
    failures_in_a_row = 0
    need_sleep = 0
    stop_reason = ""
    total = len(codes)

    for index, code in enumerate(codes):
        if stop_reason:
            previous = results.get(code)
            if not previous or previous["status"] != STATUS_ERROR:
                # the reason is told once, by the request that failed
                results[code] = make_result(code, STATUS_SKIPPED)
            continue

        print("{}".format(code), end="", flush=True)
        result = None

        if use_cache:
            feature, mtime = load_cached(output, code)
            if feature:
                result = make_result(code, feature_status(feature), feature, cached=True, retrieved=mtime)
                print(" - %s (saved earlier)" % STATUS_TITLES[result["status"]], end="")

        if result is None:
            try:
                sleep(need_sleep)
                need_sleep = delay
                area = _request_area(code, with_log, **kwargs)
                failures_in_a_row = 0
                message = ""
                if area.status == STATUS_NO_BOUNDS:
                    message = "the object is in the registry, but its boundaries are not"
                elif area.status == STATUS_NOT_FOUND:
                    message = "the service has no object with this number"
                    if area.candidates:
                        message += " (it returned: %s)" % ", ".join(area.candidates[:5])
                result = make_result(code, area.status, area.feature, message, retrieved=time.time())
                print(" - %s" % STATUS_TITLES[area.status], end="")
                if area.feature:
                    areas.append(area)
                    area_json_output(output, area)
            except Exception as er:
                failures_in_a_row += 1
                result = make_result(code, STATUS_ERROR, message=describe_error(er))
                print(" - error", end="")
                if isinstance(er, HTTPForbiddenException):
                    stop_reason = result["message"]
                elif failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                    stop_reason = "%i requests failed in a row, the last error: %s" % (
                        failures_in_a_row,
                        result["message"],
                    )

        results[code] = result
        print(", %i%%" % ((index + 1) / total * 100))
        if result["status"] == STATUS_ERROR:
            print("    %s" % result["message"])

    return stop_reason


__all__ = ["batch_parser", "read_codes", "split_codes", "make_result", "load_cached", "cache_path", "make_output"]
