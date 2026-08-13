"""Tests for the Editor Layer Index entry emitter.

The entry is submitted to a third-party index whose CI validates it against a
schema, so the shape is a contract with someone else's repo: id charset, the
``{zoom}/{x}/{y}`` placeholders (not iD's ``{z}``), the attribution object, and
an overlay flag that decides whether editors draw it over imagery or instead of
it. These tests pin all four.
"""

import io
import json
import os
import tempfile
from contextlib import contextmanager, redirect_stdout

from addresslayerist.config import Config
from addresslayerist.eli import build_eli


@contextmanager
def raises(exc_type, match):
    """Assert the block raises ``exc_type`` mentioning ``match``.

    Hand-rolled so this file runs under bare python like its neighbours.
    """
    try:
        yield
    except exc_type as exc:
        assert match in str(exc), f"{match!r} not in {str(exc)!r}"
    else:
        raise AssertionError(f"expected {exc_type.__name__} mentioning {match!r}")


def _cfg(tmp, **overrides):
    kwargs = dict(
        slug="toronto",
        provider="City of Toronto",
        title="Toronto Address Points",
        pages_url="https://skfd.github.io/toronto-addresses-layer",
        attribution="Contains information licensed under the Open Government "
                    "Licence -- Toronto",
        dataset_page="https://open.toronto.ca/dataset/address-points/",
        license_url="https://open.toronto.ca/open-data-licence/",
        country_code="CA",
        raster_zooms=[16, 17, 18, 19],
        project_dir=tmp,
    )
    kwargs.update(overrides)
    return Config(**kwargs)


def _meta(cfg, **bbox):
    os.makedirs(cfg.data_dir, exist_ok=True)
    meta = {"count": 100, "min_lon": -79.64, "min_lat": 43.58,
            "max_lon": -79.11, "max_lat": 43.86}
    meta.update(bbox)
    with open(cfg.meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)


def _build(tmp, **overrides):
    cfg = _cfg(tmp, **overrides)
    if not cfg.boundary:
        _meta(cfg)
    with open(build_eli(cfg), encoding="utf-8") as f:
        return json.load(f)


def test_entry_is_a_feature_with_the_raster_tile_url():
    with tempfile.TemporaryDirectory() as tmp:
        entry = _build(tmp)
    props = entry["properties"]
    assert entry["type"] == "Feature"
    assert props["type"] == "tms"
    # ELI templates use {zoom}, not iD's {z}; y is unflipped (XYZ, not TMS y).
    assert props["url"] == (
        "https://skfd.github.io/toronto-addresses-layer"
        "/tiles/raster/{zoom}/{x}/{y}.png"
    )
    assert (props["min_zoom"], props["max_zoom"]) == (16, 19)
    # Transparent label tiles: they go over imagery, not instead of it.
    assert props["overlay"] is True
    assert props["category"] == "other"
    assert props["country_code"] == "CA"


def test_zoom_range_follows_the_configured_raster_zooms():
    with tempfile.TemporaryDirectory() as tmp:
        props = _build(tmp, raster_zooms=[17, 18])["properties"]
    assert (props["min_zoom"], props["max_zoom"]) == (17, 18)


def test_default_id_is_derived_from_the_slug_and_is_schema_legal():
    with tempfile.TemporaryDirectory() as tmp:
        entry = _build(tmp, slug="greater-sudbury")
    # Ends up in the imagery_used changeset tag, so it reads as a name.
    assert entry["properties"]["id"] == "Greater-Sudbury-Addresses"


def test_explicit_eli_id_wins_and_names_the_file():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp, eli_id="Toronto-Address-Points")
        _meta(cfg)
        path = build_eli(cfg)
        assert os.path.basename(path) == "Toronto-Address-Points.geojson"
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["properties"]["id"] == "Toronto-Address-Points"


def test_attribution_is_required_and_points_at_the_dataset_page():
    with tempfile.TemporaryDirectory() as tmp:
        props = _build(tmp)["properties"]
    assert props["attribution"] == {
        "text": "Contains information licensed under the Open Government "
                "Licence -- Toronto",
        "required": True,
        "url": "https://open.toronto.ca/dataset/address-points/",
    }
    assert props["license_url"] == "https://open.toronto.ca/open-data-licence/"


def test_attribution_falls_back_to_the_licence_url():
    with tempfile.TemporaryDirectory() as tmp:
        props = _build(tmp, dataset_page="")["properties"]
    assert props["attribution"]["url"] == "https://open.toronto.ca/open-data-licence/"


def test_a_pages_host_gets_githubs_privacy_policy():
    with tempfile.TemporaryDirectory() as tmp:
        props = _build(tmp)["properties"]
    assert "github.com" in props["privacy_policy_url"]


def test_privacy_policy_is_omitted_for_an_unknown_host():
    with tempfile.TemporaryDirectory() as tmp:
        props = _build(tmp, pages_url="https://addresses.example.org")["properties"]
    assert "privacy_policy_url" not in props


def test_configured_privacy_policy_wins():
    with tempfile.TemporaryDirectory() as tmp:
        props = _build(tmp, privacy_policy_url="https://example.org/privacy")["properties"]
    assert props["privacy_policy_url"] == "https://example.org/privacy"


def test_geometry_falls_back_to_the_data_bbox():
    with tempfile.TemporaryDirectory() as tmp:
        geom = _build(tmp)["geometry"]
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1]                      # closed
    assert len(ring) == 5
    assert min(x for x, _ in ring) == -79.64
    assert max(y for _, y in ring) == 43.86


def test_a_boundary_polygon_replaces_the_bbox():
    square = [[[-79.5, 43.6], [-79.4, 43.6], [-79.4, 43.7],
               [-79.5, 43.7], [-79.5, 43.6]]]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "boundary.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"type": "Feature", "properties": {}, "geometry": {
                "type": "Polygon", "coordinates": square}}, f)
        # No meta written: a boundary makes the slim bbox unnecessary.
        geom = _build(tmp, boundary="boundary.geojson")["geometry"]
    assert geom == {"type": "Polygon", "coordinates": square}


def test_several_boundary_features_become_one_multipolygon():
    def square(lon):
        return [[[lon, 43.6], [lon + 0.1, 43.6], [lon + 0.1, 43.7],
                 [lon, 43.7], [lon, 43.6]]]

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "islands.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {},
                 "geometry": {"type": "Polygon", "coordinates": square(-79.5)}},
                {"type": "Feature", "properties": {},
                 "geometry": {"type": "MultiPolygon",
                              "coordinates": [square(-79.2)]}},
            ]}, f)
        geom = _build(tmp, boundary="islands.geojson")["geometry"]
    assert geom["type"] == "MultiPolygon"
    assert geom["coordinates"] == [square(-79.5), square(-79.2)]


def test_a_boundary_without_an_area_geometry_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "point.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"type": "Point", "coordinates": [-79.4, 43.7]}, f)
        with raises(RuntimeError, match="Polygon"):
            _build(tmp, boundary="point.geojson")


def test_no_bbox_and_no_boundary_asks_for_slim():
    with tempfile.TemporaryDirectory() as tmp:
        with raises(RuntimeError, match="slim"):
            build_eli(_cfg(tmp))


def test_a_layer_with_no_published_url_cannot_be_submitted():
    with tempfile.TemporaryDirectory() as tmp:
        with raises(RuntimeError, match="pages_url"):
            _build(tmp, pages_url="")


def test_a_bad_country_code_is_caught_before_the_index_ci_sees_it():
    with tempfile.TemporaryDirectory() as tmp:
        with raises(RuntimeError, match="country_code"):
            _build(tmp, country_code="Canada")


def test_an_unknown_category_is_caught():
    with tempfile.TemporaryDirectory() as tmp:
        with raises(RuntimeError, match="eli_category"):
            _build(tmp, eli_category="addresses")


def test_missing_optional_fields_warn_instead_of_failing():
    log = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, redirect_stdout(log):
        props = _build(tmp, country_code="", attribution="",
                       license_url="", dataset_page="")["properties"]
    # A first run should list every gap at once, not fail on the first one.
    for key in ("country_code", "attribution", "license_url", "boundary"):
        assert key in log.getvalue()
    for key in ("country_code", "attribution", "license_url"):
        assert key not in props


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            with redirect_stdout(io.StringIO()):
                fn()
            print(f"ok  {name}")
    print("All ELI tests passed.")
