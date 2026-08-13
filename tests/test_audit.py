"""Tests for the hidden-address check (see audit.py).

The check answers one question at the deepest rendered zoom: is every address
actually readable there?  Two ways it can fail -- a dot sitting on another
address's dot, and a label that placement dropped -- and both are permanent,
because clients upscale past the deepest zoom instead of fetching a deeper tile.
"""

import csv
import os
import tempfile

from PIL import ImageFont

from addresslayerist import audit, raster
from addresslayerist.config import ENGINE_ASSETS_DIR, Config

_FONT_FILE = os.path.join(ENGINE_ASSETS_DIR, "font", "DejaVuSans.ttf")
FONTS = {raster.FONT_SIZE: ImageFont.truetype(_FONT_FILE, raster.FONT_SIZE)}


def _points(coords, text=None, street="REDSTONE CRES"):
    """Render points for the given pixel coords, numbered like ``_rows``."""
    return [(x, y, str(2127 + 2 * i) if text is None else text, street)
            for i, (x, y) in enumerate(coords)]


def _rows(n, street="REDSTONE CRES"):
    return [(-79.7, 43.45, str(2127 + 2 * i), "", street) for i in range(n)]


def test_dots_on_the_same_spot_are_stacked():
    # The data problem the check exists for: doors geocoded to one centroid.
    points = _points([(1000.0, 1000.0)] * 3)
    stacked = audit._stacked(points, audit.STACK_DISTANCE)
    assert stacked == {0: 2, 1: 2, 2: 2}


def test_dots_a_dot_apart_are_not_stacked():
    # Touching but distinguishable: 4px between centres at 2px radius.
    points = _points([(1000.0, 1000.0), (1000.0 + 2 * raster.DOT_RADIUS, 1000.0)])
    assert audit._stacked(points, audit.STACK_DISTANCE) == {}


def test_stacking_is_found_across_hash_cells():
    # Two dots 1px apart either side of a cell boundary must still be compared,
    # or the finding depends on where the city happens to sit in pixel space.
    cell = audit.STACK_DISTANCE
    points = _points([(cell - 0.5, cell - 0.5), (cell + 0.5, cell + 0.5)])
    assert audit._stacked(points, cell) == {0: 1, 1: 1}


def test_a_dropped_label_is_unlabelled():
    points = _points([(1000.0, 1000.0), (1002.0, 1000.0)])
    assert audit._unlabelled(points, [("lm", 4, 0, False, 11), None]) == {1: "2129"}


def test_an_address_with_no_number_is_not_a_placement_failure():
    # No text means nothing was ever going to be drawn -- not a dropped label.
    points = _points([(1000.0, 1000.0)], text="")
    assert audit._unlabelled(points, [None]) == {}


def test_findings_sort_by_street_then_number():
    rows = [(-79.7, 43.45, "100", "", "B ST"),
            (-79.7, 43.45, "9", "", "B ST"),
            (-79.7, 43.45, "5", "", "A ST")]
    findings = audit._findings(rows, {0: 1, 1: 1, 2: 1}, {})
    assert [rows[i][4] + " " + rows[i][2] for i, _ in findings] == [
        "A ST 5", "B ST 9", "B ST 100",
    ]


def test_units_of_one_complex_sort_numerically_too():
    # A tower fills the report with one street number and many units; lexical
    # order would list door 10 between 1 and 2.
    rows = [(-79.7, 43.45, "2441", u, "GREENWICH DR") for u in ("2", "10", "1")]
    findings = audit._findings(rows, {0: 2, 1: 2, 2: 2}, {})
    assert [rows[i][3] for i, _ in findings] == ["1", "2", "10"]


def test_both_problems_on_one_address_are_reported_together():
    rows = _rows(1)
    findings = audit._findings(rows, {0: 3}, {0: "2127"})
    assert findings == [(0, "stacked+unlabelled")]


def _report(rows, points, placements, labelled=True, zoom=19):
    """Run the check in a throwaway project dir; return (counts, csv rows)."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(slug="testville", provider="Test", project_dir=tmp)
        counts = audit.report(cfg, zoom, rows, points, placements, labelled)
        if counts["path"] is None:
            return counts, []
        with open(counts["path"], encoding="utf-8", newline="") as f:
            return counts, list(csv.DictReader(f))


def test_a_clean_layer_reports_nothing_and_writes_no_file():
    rows = _rows(3)
    points = _points([(1000.0 + i * 100.0, 1000.0) for i in range(3)])
    placements = [("lm", 4, 0, False, 11)] * 3
    counts, csv_rows = _report(rows, points, placements)
    assert counts == {"stacked": 0, "unlabelled": 0, "addresses": 0, "path": None}
    assert csv_rows == []


def test_the_report_names_the_addresses_that_are_hidden():
    rows = _rows(3)
    points = _points([(1000.0, 1000.0), (1000.0, 1000.0), (1400.0, 1000.0)])
    placements = [("lm", 4, 0, False, 11), None, ("lm", 4, 0, False, 11)]
    counts, csv_rows = _report(rows, points, placements)
    assert counts["stacked"] == 2 and counts["unlabelled"] == 1
    assert counts["addresses"] == 2
    assert counts["path"].endswith(os.path.join("build", "testville-hidden-z19.csv"))
    assert [(r["housenumber"], r["problem"], r["stacked_with"], r["label"])
            for r in csv_rows] == [
        ("2127", "stacked", "1", ""),
        ("2129", "stacked+unlabelled", "1", "2129"),
    ]
    assert csv_rows[0]["street"] == "REDSTONE CRES"
    assert csv_rows[0]["lon"] == "-79.7000000"


def test_an_unlabelled_zoom_is_only_checked_for_stacking():
    # z16 draws dots and no labels, so "no label here" is not a finding.
    rows = _rows(2)
    points = _points([(1000.0, 1000.0), (1400.0, 1000.0)])
    counts, _ = _report(rows, points, [None, None], labelled=False, zoom=16)
    assert counts["unlabelled"] == 0 and counts["addresses"] == 0


def test_a_real_dense_cluster_is_caught_end_to_end():
    # The engine's own placement on a cluster that genuinely cannot be labelled:
    # what it drops is exactly what the check must name.
    points = [(1000.0 + i * 3.0, 1000.0, str(100 + i), "SOME ST") for i in range(30)]
    rows = [(-79.7, 43.45, str(100 + i), "", "SOME ST") for i in range(30)]
    placements, stats = raster._place_labels(points, FONTS)
    counts, csv_rows = _report(rows, points, placements)
    assert counts["unlabelled"] == stats["dropped"] > 0
    assert counts["stacked"] == 30          # 3px apart: every dot touches a neighbour
    assert len(csv_rows) == 30


def test_read_points_hands_back_the_source_row_for_every_point():
    # The report can only name an address if reading kept it.
    import json

    fd, path = tempfile.mkstemp(suffix=".geojsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for number, unit in (("2441", "3"), ("2454", "")):
            f.write(json.dumps({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-79.7, 43.45]},
                "properties": {"housenumber": number, "unit": unit,
                               "street": "GREENWICH DR"},
            }) + "\n")
    try:
        points, complexes, rows = raster._read_points(path, 19)
    finally:
        os.remove(path)
    assert len(rows) == len(points) == len(complexes) == 2
    assert rows[0] == (-79.7, 43.45, "2441", "3", "GREENWICH DR")
    assert rows[1] == (-79.7, 43.45, "2454", "", "GREENWICH DR")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("All hidden-address check tests passed.")
