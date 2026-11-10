#!/usr/bin/env python
# coding: utf-8
import argparse
import importlib.metadata
import os
import pathlib
import signal
import sys
import time
import webbrowser

from .batch import batch_parser, make_result, read_codes
from .parser import STATUS_ERROR, STATUS_NO_BOUNDS, STATUS_NOT_FOUND, TYPES, Area
from .utils import clean_code, code_to_filename
from .visualize import MapDependencyError, load_features, results_from_features, write_html, write_png

try:
    __version__ = importlib.metadata.version("rosreestr2coord")
except importlib.metadata.PackageNotFoundError:
    # started from the source tree without installation
    __version__ = "unknown"


def getopts(argv=None):
    """
    Get the command line options.
    """
    parser = argparse.ArgumentParser(
        description="Get geojson with coordinates of area by cadastral number (based on https://nspd.gov.ru/map)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-c", "--code", type=str, help="area cadastral number")
    parser.add_argument(
        "-t",
        "--area_type",
        type=int,
        default=1,
        help=f"area types: {', '.join(f'{k}: {v}' for k, v in TYPES.items())}",
    )
    parser.add_argument("-p", "--path", type=str, help="media path")
    parser.add_argument("-o", "--output", type=str, default=os.path.join(os.getcwd(), "output"), help="output path")
    parser.add_argument("-l", "--list", type=str, help="path of file with cadastral codes list")
    parser.add_argument(
        "-m",
        "--map",
        action="store_true",
        help="make a map of the result: an interactive html page and, if matplotlib is installed, a png picture",
    )
    parser.add_argument(
        "--map-from",
        nargs="+",
        metavar="PATH",
        help="make the map from saved geojson files or directories with them instead of requesting the service "
        "(with --list or --code only these numbers are shown)",
    )
    parser.add_argument("--title", type=str, help="title of the map")
    parser.add_argument("-d", "--display", action="store_true", help="open the map in the browser (implies --map)")
    parser.add_argument("-D", "--delay", type=float, default=1, help="delay between requests (only for --list mode)")
    parser.add_argument("-r", "--refresh", action="store_true", help="do not use cache")
    parser.add_argument("-T", "--timeout", type=float, default=5, help="time to wait for an answer, seconds")
    parser.add_argument("-P", "--proxy", action="store_true", help="use proxies")
    parser.add_argument("-v", "--version", action="store_true", help="show current version")
    parser.add_argument("-u", "--proxy_url", type=str, help="set proxy URL")
    return parser.parse_args(argv)


def read_list_file(file_path):
    # utf-8-sig: a list saved by Windows Notepad starts with a byte order mark
    with open(file_path, "r", encoding="utf-8-sig") as file:
        return file.readlines()


def list_name(file_path):
    return os.path.splitext(os.path.basename(file_path))[0]


def default_title(name):
    return "Cadastral objects: %s" % name


def handle_batch_processing(file_path, output, delay, kwargs):
    codes = read_list_file(file_path)
    return batch_parser(codes, output=output, delay=delay, file_name=list_name(file_path), **kwargs)


def get_by_code(code, output, display=False, **kwargs):
    area = Area(code, **kwargs)
    abspath = os.path.abspath(output)
    process_area(area, abspath, display)
    return area


def process_area(area, output_path, display=False):
    geojson = area.to_geojson()

    file_name = code_to_filename(area.file_name)

    if geojson:
        save_file(geojson, output_path, file_name, "geojson")

        kml = area.to_kml()
        if kml:
            save_file(kml, output_path, file_name, "kml")
    if display:
        make_maps([area_result(area)], output_path, file_name, default_title(area.code), display=True)


def area_result(area):
    """The outcome of a single request in the form the maps take."""
    message = area.error_message
    if area.status == STATUS_NOT_FOUND:
        message = "the service has no object with this number"
        if area.candidates:
            message += " (it returned: %s)" % ", ".join(area.candidates[:5])
    elif area.status == STATUS_NO_BOUNDS:
        message = "the object is in the registry, but its boundaries are not"
    # no status: the request was not made at all
    return make_result(area.code, area.status or STATUS_ERROR, area.feature, message, retrieved=time.time())


def save_file(content, output_path, file_name, extension):
    directory = os.path.join(output_path, extension)
    os.makedirs(directory, exist_ok=True)
    file_path = os.path.join(directory, f"{file_name}.{extension}")
    if isinstance(content, str):
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)
    else:
        content.write(file_path, encoding="UTF-8", xml_declaration=True)
    print(f"{extension} - {file_path}")


def make_maps(results, output, name, title, display=False):
    """Write <output>/map/<name>.html (and .png if matplotlib is installed). Returns the path of the html map."""
    directory = os.path.join(os.path.abspath(output), "map")
    html_path = write_html(os.path.join(directory, f"{name}.html"), results, title=title)
    print(f"map - {html_path}")
    try:
        png_path = write_png(os.path.join(directory, f"{name}.png"), results, title=title)
        print(f"png - {png_path}")
    except MapDependencyError as er:
        print(f"png - skipped: {er}")
    except Exception as er:  # the picture is an extra: the html map is already written
        print(f"png - failed: {er}")
    if display:
        webbrowser.open(pathlib.Path(html_path).as_uri())
    return html_path


def map_from_files(opt):
    """Make the map from files saved earlier, without requests to the service."""
    codes = None
    if opt.list:
        codes = read_codes(read_list_file(opt.list))
        name = list_name(opt.list)
    elif opt.code:
        codes = [clean_code(opt.code)]
        name = code_to_filename(codes[0])
    else:
        first = os.path.normpath(opt.map_from[0])
        name = os.path.splitext(os.path.basename(first))[0] or "map"
    features = load_features(opt.map_from)
    results = results_from_features(features, codes)
    print("Objects read from the files: %i" % len(features))
    if not results:
        print("There is nothing to show: no objects in %s" % ", ".join(opt.map_from))
        return None
    return make_maps(results, opt.output, name, opt.title or default_title(name), display=opt.display)


def run_console(opt):
    kwargs = {
        "media_path": opt.path,
        "with_proxy": opt.proxy,
        "area_type": opt.area_type,
        "use_cache": not opt.refresh,
        "coord_out": "EPSG:4326",
        "proxy_url": opt.proxy_url,
        "timeout": opt.timeout,
    }
    with_map = opt.map or opt.display

    if opt.map_from:
        map_from_files(opt)
    elif opt.list:
        results = handle_batch_processing(opt.list, opt.output, opt.delay, kwargs)
        if with_map:
            name = list_name(opt.list)
            make_maps(results, opt.output, name, opt.title or default_title(name), display=opt.display)
    elif opt.code:
        area = get_by_code(opt.code, opt.output, **kwargs)
        if with_map:
            make_maps(
                [area_result(area)],
                opt.output,
                code_to_filename(area.file_name),
                opt.title or default_title(area.code),
                display=opt.display,
            )
    else:
        print("Nothing to do: set a cadastral number (-c), a file with a list of them (-l) or --map-from. See -h")


def console():
    opt = getopts()
    if opt.version:
        print(__version__)
        return

    def signal_handler(signalnum, frame):
        print("You pressed Ctrl+C")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    print("Press Ctrl+C to exit")

    run_console(opt)
