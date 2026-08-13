"""Tests for the locked slim/MVT property-map derivation.

The whole point of the engine is that no city needs custom slim/raster code:
the slim output schema is derived from the canonical [fields] map. These tests
pin that contract.
"""

import json
import os
import tempfile
from types import SimpleNamespace

from addresslayerist.slim import _property_map, slim


def _cfg(fields, mvt_extra=None):
    return SimpleNamespace(fields=fields, mvt_extra=mvt_extra or {})


def _run_slim(fields, props, mvt_extra=None):
    """Slim one feature and return its output properties."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.geojson")
        with open(src, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-79.7, 43.4]},
                "properties": props,
            }]}, f)
        cfg = SimpleNamespace(
            fields=fields, mvt_extra=mvt_extra or {}, expected_min=0,
            data_dir=tmp,
            slim_path=os.path.join(tmp, "out.geojsonl"),
            meta_path=os.path.join(tmp, "meta.json"),
        )
        slim(cfg, src)
        with open(cfg.slim_path, encoding="utf-8") as f:
            return json.loads(f.readline())["properties"]


def test_canonical_fields_map_to_short_keys():
    cfg = _cfg({
        "number": "STREET_NUM",
        "street": "SNAME",
        "unit": "UNIT",
        "full": "ADDRESS",
    })
    assert _property_map(cfg) == {
        "STREET_NUM": "housenumber",
        "SNAME": "street",
        "UNIT": "unit",
        "ADDRESS": "addr",
    }


def test_partial_fields_only_map_what_is_present():
    cfg = _cfg({"street": "STREET_NM", "full": "CIVIC_ADDR"})
    assert _property_map(cfg) == {"STREET_NM": "street", "CIVIC_ADDR": "addr"}


def test_mvt_extra_is_merged_through():
    cfg = _cfg({"number": "N"}, mvt_extra={"ADDRESS_CLASS": "class"})
    assert _property_map(cfg) == {"N": "housenumber", "ADDRESS_CLASS": "class"}


def test_unknown_canonical_keys_are_ignored():
    cfg = _cfg({"number": "N", "postcode": "PC"})  # postcode is not in the schema
    assert _property_map(cfg) == {"N": "housenumber"}


def test_suffix_is_not_a_slim_key_of_its_own():
    # It is folded into housenumber, so it must not widen the schema.
    cfg = _cfg({"number": "N", "suffix": "SFX"})
    assert _property_map(cfg) == {"N": "housenumber"}


def test_suffix_is_folded_into_the_housenumber_and_the_label():
    props = _run_slim(
        {"number": "STREET_NUM", "street": "SNAME", "suffix": "SUFFIX"},
        {"STREET_NUM": "335", "SNAME": "LAKESHORE RD E", "SUFFIX": "A"},
    )
    assert props["housenumber"] == "335A"
    assert props["name"] == "335A"
    assert "suffix" not in props


def test_unsuffixed_row_of_a_suffixed_city_is_untouched():
    props = _run_slim(
        {"number": "STREET_NUM", "suffix": "SUFFIX"},
        {"STREET_NUM": "335", "SUFFIX": " "},
    )
    assert props["housenumber"] == "335"


def test_mvt_extra_suffix_tag_does_not_reach_the_housenumber():
    # Toronto passes LO_NUM_SUF through as a tag; its number already includes it.
    props = _run_slim(
        {"number": "ADDRESS_NUMBER"},
        {"ADDRESS_NUMBER": "246A", "LO_NUM_SUF": "A"},
        mvt_extra={"LO_NUM_SUF": "suffix"},
    )
    assert props["housenumber"] == "246A"
    assert props["name"] == "246A"
    assert props["suffix"] == "A"      # still published as a tile tag


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("All slim-mapping tests passed.")
