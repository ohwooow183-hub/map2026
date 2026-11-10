import pytest
from rosreestr2coord.attributes import (
    attribute_rows,
    feature_number,
    feature_numbers,
    format_value,
    summarize,
    to_number,
)

from .nspd_mock import make_feature

pytestmark = pytest.mark.unit


def test_summary_of_a_real_land_plot(sample):
    info = summarize(sample)

    assert info["number"] == "50:33:0000000:98325"
    assert info["category"] == "Земельные участки ЕГРН"
    assert info["kind"] == "Земельный участок"
    assert info["subtype"] == "Многоконтурный участок"
    assert info["area"] == 22318
    assert info["area_kind"] == "surveyed"
    assert info["address"].endswith("г.о. Ступино")
    assert info["land_category"] == "Земли сельскохозяйственного назначения"
    assert info["use"] == "Для сельскохозяйственного производства"


def test_summary_of_nothing_is_empty():
    info = summarize(None)
    assert info["number"] == "" and info["area"] is None and info["cost"] is None and info["address"] == ""


def test_declared_area_is_used_when_the_plot_is_not_surveyed():
    feature = make_feature("99:01:0101001:13", geometry="Point", specified_area=None, declared_area="1 500,5")
    info = summarize(feature)
    assert info["area"] == 1500.5
    assert info["area_kind"] == "declared"


def test_zero_cost_means_unknown():
    assert summarize(make_feature("99:01:0101001:11", cost_value=0))["cost"] is None
    assert summarize(make_feature("99:01:0101001:11", cost_value=271540.5))["cost"] == 271540.5


def test_numbers_of_a_feature():
    feature = make_feature("99:01:0101001:11")
    assert feature_number(feature) == "99:01:0101001:11"
    assert feature_numbers(feature) == ["99:01:0101001:11"]
    assert feature_number(None) == "" and feature_numbers({}) == []


def test_rows_have_readable_labels_and_skip_empty_values(sample):
    rows = attribute_rows(sample)
    labels = [label for label, _ in rows]
    values = dict(rows)

    assert labels[0] == "Layer"
    assert values["Cadastral number"] == "50:33:0000000:98325"
    assert values["Area, surveyed (m²)"] == "22\u202f318"
    assert values["Land category"] == "Земли сельскохозяйственного назначения"
    assert all(value.strip() for _, value in rows)
    # keys without a translation are still shown, technical ones are not
    assert "geocoderObject" not in labels and "no_coords" not in labels


def test_unknown_attributes_are_kept_under_their_own_names():
    rows = dict(attribute_rows(make_feature("99:01:0101001:11", some_new_key="значение", empty_key="")))
    assert rows["some_new_key"] == "значение"
    assert "empty_key" not in rows


def test_only_quantities_are_grouped():
    feature = make_feature("99:01:0101001:55", year_built=1987, build_record_area=12345.6, specified_area=None)
    rows = dict(attribute_rows(feature))
    assert rows["Year built"] == "1987"
    assert rows["Total area (m²)"] == "12\u202f345.6"


@pytest.mark.parametrize(
    "value,expected",
    [
        (22318, "22318"),
        (22318.0, "22318"),
        (12.5, "12.5"),
        (True, "yes"),
        ("2025-12-19T00:00:00", "2025-12-19"),
        ("2025-12-19T10:30:00", "2025-12-19T10:30:00"),
        ("  текст ", "текст"),
        (["a", "", "b"], "a, b"),
    ],
)
def test_format_value(value, expected):
    assert format_value(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (1500, 1500.0),
        ("1500", 1500.0),
        ("1 500,5", 1500.5),
        ("1\u00a0500", 1500.0),
        ("-", None),
        (None, None),
        (True, None),
    ],
)
def test_to_number(value, expected):
    assert to_number(value) == expected
