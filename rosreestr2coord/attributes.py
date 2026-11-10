"""
What the properties of an NSPD feature mean.

NSPD returns the registry attributes of an object in `properties.options`. The set of keys depends on the kind of
the object (land plot, building, construction, ...). This module turns them into a short uniform summary and into
a readable list of rows for tables and popups.
"""

from typing import Any, Dict, List, Optional, Tuple

# key -> label, in the order in which the rows are shown.
# Keys of the layers "Земельные участки ЕГРН", "Здания", "Сооружения", "Объекты незавершенного строительства".
OPTION_LABELS: Dict[str, str] = {
    "cad_num": "Cadastral number",
    "cad_number": "Cadastral number",
    "land_record_type": "Object type",
    "build_record_type_value": "Object type",
    "object_type_value": "Object type",
    "object_under_construction_record_record_type_value": "Object type",
    "land_record_subtype": "Land plot type",
    "building_name": "Name",
    "params_name": "Name",
    "object_under_construction_record_name": "Name",
    "readable_address": "Address",
    "address_readable_address": "Address",
    "specified_area": "Area, surveyed (m²)",
    "declared_area": "Area, declared (m²)",
    "area": "Area (m²)",
    "build_record_area": "Total area (m²)",
    "params_area": "Area (m²)",
    "params_built_up_area": "Built-up area (m²)",
    "built_up_area": "Built-up area (m²)",
    "land_record_category_type": "Land category",
    "permitted_use_established_by_document": "Permitted use",
    "purpose": "Purpose",
    "params_purpose": "Purpose",
    "status": "Status",
    "common_data_status": "Status",
    "object_previously_posted": "Status",
    "ownership_type": "Ownership",
    "cost_value": "Cadastral value (RUB)",
    "cost_index": "Cadastral value per m² (RUB)",
    "land_record_reg_date": "Registered on",
    "build_record_registration_date": "Registered on",
    "registration_date": "Registered on",
    "quarter_cad_number": "Cadastral quarter",
    "floors": "Floors (incl. underground)",
    "params_floors": "Floors (incl. underground)",
    "underground_floors": "Underground floors",
    "params_underground_floors": "Underground floors",
    "materials": "Wall material",
    "year_built": "Year built",
    "params_year_built": "Year built",
    "year_commisioning": "Year commissioned",
    "params_year_commisioning": "Year commissioned",
    "degree_readiness": "Readiness (%)",
    "params_height": "Height (m)",
    "height": "Height (m)",
    "params_depth": "Depth (m)",
    "depth": "Depth (m)",
    "params_occurence_depth": "Occurrence depth (m)",
    "occurence_depth": "Occurrence depth (m)",
    "params_extension": "Length (m)",
    "extension": "Length (m)",
    "params_volume": "Volume (m³)",
    "volume": "Volume (m³)",
    "united_cad_number": "Part of the real estate complex",
    "facility_cad_number": "Part of the enterprise complex",
    "cultural_heritage_val": "Cultural heritage register",
    "cultural_heritage_object": "Cultural heritage register",
}

# Keys in which zero means "not set"
_ZERO_IS_EMPTY = ("cost_value", "cost_index")

# Quantities: their digits are grouped (22 318). Years, numbers of floors and codes are written as they are.
_QUANTITY_KEYS = (
    "specified_area",
    "declared_area",
    "area",
    "build_record_area",
    "params_area",
    "params_built_up_area",
    "built_up_area",
    "cost_value",
    "cost_index",
    "params_extension",
    "extension",
    "params_volume",
    "volume",
)

# Service keys that say nothing to a reader
_HIDDEN_OPTION_KEYS = ("geocoderObject", "no_coords")

_NUMBER_KEYS = ("cad_num", "cad_number")
_KIND_KEYS = (
    "land_record_type",
    "build_record_type_value",
    "object_type_value",
    "object_under_construction_record_record_type_value",
)
_ADDRESS_KEYS = ("readable_address", "address_readable_address")
# (key, kind of the value)
_AREA_KEYS = (
    ("specified_area", "surveyed"),
    ("declared_area", "declared"),
    ("area", ""),
    ("build_record_area", "total"),
    ("params_area", ""),
    ("params_built_up_area", "built-up"),
    ("built_up_area", "built-up"),
)
_STATUS_KEYS = ("status", "common_data_status", "object_previously_posted")
_USE_KEYS = ("permitted_use_established_by_document", "purpose", "params_purpose")
_DATE_KEYS = ("land_record_reg_date", "build_record_registration_date", "registration_date")


def is_empty(value: Any, key: str = "") -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    if key in _ZERO_IS_EMPTY and isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return True
    return False


def _first(options: dict, keys) -> Any:
    for key in keys:
        value = options.get(key)
        if not is_empty(value, key):
            return value
    return None


def to_number(value: Any) -> Optional[float]:
    """Numbers come both as numbers and as strings ("1 500,5")."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def format_value(value: Any, grouped: bool = False) -> str:
    """Plain text for any attribute value. `grouped` - write a number as 1 234 567 (for quantities)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            text = "{:,}".format(int(value))
        else:
            text = "{:,.2f}".format(value).rstrip("0").rstrip(".")
        # narrow no-break spaces between the groups of digits
        return text.replace(",", "\u202f" if grouped else "")
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(v) for v in value if not is_empty(v))
    if isinstance(value, dict):
        return "; ".join("%s: %s" % (k, format_value(v)) for k, v in value.items() if not is_empty(v))
    text = str(value).strip()
    # "2025-12-19T00:00:00" -> "2025-12-19"
    if len(text) >= 19 and text[4:5] == "-" and text[7:8] == "-" and text[10:19] == "T00:00:00":
        return text[:10]
    return text


def feature_numbers(feature: Optional[dict]) -> List[str]:
    """Every property in which the service repeats the own (cadastral / registry) number of the object."""
    props = (feature or {}).get("properties") or {}
    options = props.get("options") or {}
    values = [options.get(k) for k in _NUMBER_KEYS + ("reg_numb_border",)]
    values += [props.get(k) for k in ("label", "externalKey", "descr")]
    numbers: List[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in numbers:
            numbers.append(value.strip())
    return numbers


def feature_number(feature: Optional[dict]) -> str:
    """The own (cadastral / registry) number of the object."""
    numbers = feature_numbers(feature)
    return numbers[0] if numbers else ""


def summarize(feature: Optional[dict]) -> Dict[str, Any]:
    """The few attributes that matter in a list of objects, under the same names for every kind of object."""
    props = (feature or {}).get("properties") or {}
    options = props.get("options") or {}

    area, area_kind = None, ""
    for key, kind in _AREA_KEYS:
        number = to_number(options.get(key))
        if number:
            area, area_kind = number, kind
            break

    date = _first(options, _DATE_KEYS)
    return {
        "number": feature_number(feature),
        "category": props.get("categoryName") or "",
        "kind": _first(options, _KIND_KEYS) or "",
        "subtype": options.get("land_record_subtype") or "",
        "address": _first(options, _ADDRESS_KEYS) or "",
        "area": area,
        "area_kind": area_kind,
        "cost": to_number(_first(options, ("cost_value",))),
        "status": _first(options, _STATUS_KEYS) or "",
        "use": _first(options, _USE_KEYS) or "",
        "land_category": options.get("land_record_category_type") or "",
        "ownership": options.get("ownership_type") or "",
        "quarter": options.get("quarter_cad_number") or "",
        "registered": format_value(date) if date else "",
    }


def attribute_rows(feature: Optional[dict]) -> List[Tuple[str, str]]:
    """
    All non-empty attributes of the object as (label, value) rows:
    the known ones first, under readable names, then everything else under the names used by the service.
    """
    props = (feature or {}).get("properties") or {}
    options = props.get("options") or {}
    rows: List[Tuple[str, str]] = []

    category = props.get("categoryName")
    if not is_empty(category):
        rows.append(("Layer", format_value(category)))

    seen = set()
    for key, label in OPTION_LABELS.items():
        if key in options and not is_empty(options[key], key):
            rows.append((label, format_value(options[key], grouped=key in _QUANTITY_KEYS)))
            seen.add(key)
    for key, value in options.items():
        if key in seen or key in OPTION_LABELS or key in _HIDDEN_OPTION_KEYS or is_empty(value, key):
            continue
        rows.append((key, format_value(value)))
    return rows
