"""Tests for the shared address-label rule (see label.py).

Both tile builders label points through display_label, so this pins the text a
townhouse unit gets -- the fix for skfd/oakville-address-layer#1, where a whole
complex rendered as its street number repeated.
"""

from addresslayerist.label import display_label, housenumber


def test_suffix_joins_the_number():
    assert housenumber("335", "A") == "335A"


def test_number_alone_when_there_is_no_suffix():
    assert housenumber("335", None) == "335"
    assert housenumber("335", "  ") == "335"


def test_suffix_already_inside_the_number_is_not_doubled():
    # Toronto's ADDRESS_NUMBER reads "246A" while LO_NUM_SUF also says "A".
    assert housenumber("246A", "A") == "246A"
    assert housenumber("246a", "A") == "246a"


def test_fractional_suffix_is_parted_from_the_number():
    assert housenumber("963", "1/2") == "963 1/2"


def test_numeric_suffix_is_not_swallowed_by_a_matching_number():
    # "1235" ends with "5", but a digit suffix must not be treated as combined.
    assert housenumber("1235", "5") == "1235 5"


def test_suffixed_number_still_takes_a_unit():
    assert display_label(housenumber("335", "A"), "2") == "2-335A"


def test_number_alone_when_there_is_no_unit():
    assert display_label("2280", None) == "2280"
    assert display_label("2280", "") == "2280"
    assert display_label("2280", "  ") == "2280"


def test_unit_leads_the_number():
    assert display_label("2280", "3") == "3-2280"
    assert display_label("1500", "A1") == "A1-1500"


def test_units_in_one_complex_are_distinguishable():
    complex_labels = {display_label("2280", str(u)) for u in range(1, 41)}
    assert len(complex_labels) == 40


def test_unit_alone_when_the_number_is_missing():
    assert display_label(None, "3") == "3"


def test_nothing_to_label():
    assert display_label(None, None) == ""


def test_unit_matching_the_street_number_is_still_labelled():
    # Unit 15 of 15 Hays Blvd is a real door, not a duplicated column: dropping
    # it would leave two points in one complex both reading "15".
    assert display_label("15", "15") == "15-15"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("All label tests passed.")
