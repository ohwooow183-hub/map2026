# coding: utf-8
import copy
import csv
import json
import os
import xml.etree.cElementTree as ET

from .attributes import summarize
from .geometry import geometry_area_m2, geometry_center, iter_lines, iter_polygons


def make_output(output, file_name, file_format, out_path=""):
    out_path = out_path if out_path else file_format
    abspath = os.path.abspath(output)
    filename = "%s.%s" % (file_name, file_format)
    path = os.path.join(abspath, out_path)
    if not os.path.isdir(path):
        os.makedirs(path)
    return os.path.join(path, filename)


CSV_COLUMNS = [
    "code",
    "area",
    "cost",
    "address",
    "status",
    "note",
    "object",
    "use",
    "land_category",
    "registry_status",
    "area_by_outline",
    "lat",
    "lon",
    "coordinates",
]


def _lat_lon_coordinates(geometry):
    """Coordinates of a geometry with every position turned into [lat, lon] (the order of the old csv output)."""

    def swap(item):
        if isinstance(item, (list, tuple)) and item and isinstance(item[0], (int, float)):
            return [item[1], item[0]] + list(item[2:])
        if isinstance(item, (list, tuple)):
            return [swap(i) for i in item]
        return item

    return swap((geometry or {}).get("coordinates") or [])


def csv_row(code, feature=None, status="", note=""):
    """One line of the csv report: what is known about a requested code."""
    row = dict.fromkeys(CSV_COLUMNS, "")
    row["code"] = code
    row["status"] = status
    row["note"] = note
    if feature:
        info = summarize(feature)
        geometry = feature.get("geometry")
        center = geometry_center(geometry)
        outline_area = geometry_area_m2(geometry)
        row.update(
            {
                "area": "" if info["area"] is None else "%g" % info["area"],
                "cost": "" if info["cost"] is None else "%.2f" % info["cost"],
                "address": info["address"],
                "object": info["kind"] or info["category"],
                "use": info["use"],
                "land_category": info["land_category"],
                "registry_status": info["status"],
                "area_by_outline": "%.1f" % outline_area if outline_area else "",
                "lat": "%.7f" % center[1] if center else "",
                "lon": "%.7f" % center[0] if center else "",
                "coordinates": json.dumps(_lat_lon_coordinates(geometry)) if geometry else "",
            }
        )
    return row


def _area_row(area):
    return csv_row(
        getattr(area, "code", ""),
        feature=getattr(area, "feature", None),
        status=getattr(area, "status", None) or "",
        note=getattr(area, "error_message", "") or "",
    )


def write_csv(path, rows):
    # newline="" - otherwise the csv module produces empty lines on Windows
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def area_csv_output(output, area):
    return write_csv(make_output(output, area.file_name, "csv"), [_area_row(area)])


def batch_csv_output(output, areas, file_name):
    """`areas` - Area objects or ready rows made with `csv_row`."""
    rows = [a if isinstance(a, dict) else _area_row(a) for a in areas]
    return write_csv(make_output(output, file_name, "csv"), rows)


def batch_json_output(output, areas, file_name, with_attrs=True, crs_name="EPSG:4326"):
    """Write one FeatureCollection. `areas` - Area objects or GeoJSON features."""
    features = []
    feature_collection = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": crs_name}},
        "features": features,
    }
    path = make_output(output, file_name, "geojson")
    for a in areas:
        feature = a if isinstance(a, dict) else a.to_geojson(dumps=False)
        if feature:
            if not with_attrs:
                feature = dict(feature, properties={})
            features.append(feature)

    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(feature_collection, ensure_ascii=False))
    return path


def area_json_output(output, area, with_attrs=True):
    geojson = area.to_geojson(dumps=False)
    if geojson:
        if not with_attrs:
            geojson = dict(geojson, properties={})
        geojson = json.dumps(geojson, ensure_ascii=False)
        with open(make_output(output, area.file_name, "geojson"), "w", encoding="utf-8") as f:
            f.write(geojson)
    return geojson


def coords2geojson(coords, geom_type, crs_name, attrs=None):
    if attrs is False:
        attrs = {}

    if len(coords):
        features = []
        feature_collection = {"type": "FeatureCollection", "features": features}
        if geom_type.upper() == "POINT":
            for i in range(len(coords)):
                if coords[i]:
                    for j in range(len(coords[i])):
                        xy = coords[i][j]
                        for x, y in xy:
                            point = {
                                "type": "Feature",
                                "properties": {"hole": j > 0},
                                "geometry": {"type": "Point", "coordinates": [x, y]},
                            }
                            features.append(point)
        elif geom_type.upper() == "POLYGON":
            multi_polygon = []
            for fry in range(len(coords)):
                polygon = []
                for j in range(len(coords[fry])):
                    xy = coords[fry][j]
                    # close polygon
                    if xy[-1] != xy[0]:
                        xy.append(xy[0])
                    polygon.append(xy)
                multi_polygon.append(polygon)
            feature = {
                "type": "Feature",
                "properties": attrs,
                "geometry": {"type": "MultiPolygon", "coordinates": multi_polygon},
            }
            feature_collection = feature
        feature_collection["crs"] = {"type": "name", "properties": {"name": crs_name}}
        return feature_collection
    return False


def _kml_coordinates(positions):
    return " ".join(",".join(str(v) for v in position) for position in positions)


def _kml_document(name):
    kml = ET.Element("kml", attrib={"xmlns": "http://www.opengis.net/kml/2.2"})
    doc = ET.SubElement(kml, "Document")
    folder = ET.SubElement(doc, "Folder")
    ET.SubElement(folder, "name").text = name
    placemark = ET.SubElement(folder, "Placemark")
    ET.SubElement(placemark, "name").text = name

    style = ET.SubElement(placemark, "Style")
    line_style = ET.SubElement(style, "LineStyle")
    ET.SubElement(line_style, "color").text = "ff0000ff"
    poly_style = ET.SubElement(style, "PolyStyle")
    ET.SubElement(poly_style, "fill").text = "0"
    return kml, placemark


def _kml_name(attrs):
    attrs = attrs or {}
    return attrs.get("label") or (attrs.get("options") or {}).get("cad_num", "") or ""


def coords2kml(coords, attrs):
    """
    KML document for a list of polygons.

    `coords` - a list of polygons, each polygon is a list of rings (the first ring is the outer boundary,
    the others are holes), each ring is a list of [lon, lat] positions. The input is not modified.
    """
    if len(coords):
        kml, placemark = _kml_document(_kml_name(attrs))
        multi_geometry = ET.SubElement(placemark, "MultiGeometry")

        for polygon_coords in coords:
            polygon = ET.SubElement(multi_geometry, "Polygon")
            for j, ring in enumerate(polygon_coords):
                if not ring:
                    continue
                # for holes
                boundary = ET.SubElement(polygon, "innerBoundaryIs" if j else "outerBoundaryIs")
                xy = copy.copy(list(ring))
                if xy[0] != xy[-1]:
                    xy.append(xy[0])
                linear_ring = ET.SubElement(boundary, "LinearRing")
                ET.SubElement(linear_ring, "coordinates").text = _kml_coordinates(xy)
        return ET.ElementTree(kml)
    return False


def feature2kml(feature):
    """KML document for a GeoJSON feature with a (Multi)Polygon, (Multi)LineString or Point geometry (WGS84)."""
    geometry = (feature or {}).get("geometry") or {}
    attrs = (feature or {}).get("properties") or {}

    polygons = list(iter_polygons(geometry))
    if polygons:
        return coords2kml(polygons, attrs)

    lines = list(iter_lines(geometry))
    if lines:
        kml, placemark = _kml_document(_kml_name(attrs))
        multi_geometry = ET.SubElement(placemark, "MultiGeometry")
        for line in lines:
            line_string = ET.SubElement(multi_geometry, "LineString")
            ET.SubElement(line_string, "coordinates").text = _kml_coordinates(line)
        return ET.ElementTree(kml)

    if geometry.get("type") == "Point" and len(geometry.get("coordinates") or []) >= 2:
        kml, placemark = _kml_document(_kml_name(attrs))
        point = ET.SubElement(placemark, "Point")
        ET.SubElement(point, "coordinates").text = _kml_coordinates([geometry["coordinates"]])
        return ET.ElementTree(kml)
    return False
