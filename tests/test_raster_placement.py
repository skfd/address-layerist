"""Tests for raster label placement (see raster.py).

These pin the rule from skfd/oakville-address-layer#2: a label may never be
drawn over another address's dot.  The geometry in the row test is the real
2127-2137 REDSTONE CRES case at z19 -- five dots ~30px apart on one line, each
label 28px wide, where the reporter found a dot with no number beside it.
"""

from PIL import ImageFont

from addresslayerist import raster
from addresslayerist.config import ENGINE_ASSETS_DIR, Config

import json
import os
import tempfile

_FONT_FILE = os.path.join(ENGINE_ASSETS_DIR, "font", "DejaVuSans.ttf")


def fonts(*sizes):
    """A size -> font ladder, as _place_labels expects."""
    return {s: ImageFont.truetype(_FONT_FILE, s) for s in (sizes or (raster.FONT_SIZE,))}


FONTS = fonts(raster.FONT_SIZE)
FONT = FONTS[raster.FONT_SIZE]


def _label_boxes(points, placements, ladder=None):
    """The drawn text boxes only -- no leader, no padding."""
    ladder = ladder or FONTS
    for i, placement in enumerate(placements):
        if placement is None or not points[i][2]:
            continue
        gx, gy, text, _st = points[i]
        anchor, dx, dy, _leader, size = placement
        yield i, raster._anchor_bbox(
            anchor, gx + dx, gy + dy, ladder[size].getlength(text), size
        )


def _dots_covered(points, placements, ladder=None):
    """Indices of dots that some *other* point's label box overlaps."""
    covered = []
    r = raster.DOT_RADIUS
    boxes = list(_label_boxes(points, placements, ladder))
    for j, (gx, gy, _t, _s) in enumerate(points):
        for i, (x0, y0, x1, y1) in boxes:
            if i != j and x0 < gx + r and x1 > gx - r and y0 < gy + r and y1 > gy - r:
                covered.append(j)
                break
    return covered


def _row(n=5, spacing=30.0, y=1000.0, x0=1000.0):
    """A straight row of evenly spaced points, as townhouses render."""
    return [(x0 + i * spacing, y, str(2127 + 2 * i), "REDSTONE CRES") for i in range(n)]


def test_every_point_in_a_tight_row_gets_a_label():
    points = _row()
    placements, stats = raster._place_labels(points, FONTS)
    assert stats["dropped"] == 0
    assert all(p is not None for p in placements)


def test_no_label_covers_another_dot_in_a_tight_row():
    # The bug: 30px labels in a 30px gap, so each label buried the next dot.
    points = _row()
    placements, _ = raster._place_labels(points, FONTS)
    assert _dots_covered(points, placements) == []


def test_a_label_may_still_hug_its_own_dot():
    # The owner tag exists so a point's own seeded dot does not veto its label;
    # a lone point must still get a no-leader inner slot.
    points = [(1000.0, 1000.0, "2133", "REDSTONE CRES")]
    placements, stats = raster._place_labels(points, FONTS)
    assert stats == {"inner": 1, "leadered": 0, "dropped": 0, "shrunk": 0}
    assert placements[0][3] is False
    assert placements[0][4] == raster.FONT_SIZE      # never shrunk in open ground


def test_leadered_placement_is_still_reachable():
    # Two dots close enough that the second cannot take an inner slot, but with
    # open space around: it should reach the ring, not be dropped.
    points = [(1000.0, 1000.0, "2133", "REDSTONE CRES"),
              (1006.0, 1000.0, "2135", "REDSTONE CRES")]
    placements, stats = raster._place_labels(points, FONTS)
    assert stats["dropped"] == 0
    assert _dots_covered(points, placements) == []


def test_unlabelled_points_still_block_labels():
    # A point with no text draws a dot but no label; it must still be an
    # obstacle, or its dot gets buried by a neighbour.
    points = [(1000.0, 1000.0, "", "REDSTONE CRES"),
              (1030.0, 1000.0, "2135", "REDSTONE CRES")]
    placements, _ = raster._place_labels(points, FONTS)
    assert _dots_covered(points, placements) == []


# A row this tight is where the font size starts to decide anything.  It used
# to be 14px, back when a leader reserved its whole bounding box; measuring the
# leader itself frees enough ground that a 14px row now labels completely at any
# size, so the tests that are *about* the size ladder had to move to a row that
# is genuinely full.  7px apart is a townhouse frontage at z17 (~6 m).
_LADDER_SPACING = 7.0


def _tight_row(n=20, spacing=_LADDER_SPACING):
    return [(1000.0 + i * spacing, 1000.0, str(2100 + 2 * i), "PELEE BLVD")
            for i in range(n)]


def test_smaller_font_labels_more_of_a_tight_row():
    # Why z17 renders at 9px: the dot spacing shrinks with the zoom but the
    # label footprint does not, so the smaller size is what keeps a dense row
    # labelled at all.
    points = _tight_row()
    _, big_stats = raster._place_labels(points, FONTS)
    _, small_stats = raster._place_labels(points, fonts(9))
    assert small_stats["dropped"] < big_stats["dropped"]


def test_shrink_ladder_rescues_what_one_size_would_drop():
    # Same row, but now the ladder is available: points that cannot fit at 11px
    # take a smaller size instead of being dropped.
    points = _tight_row()
    ladder = fonts(11, 9, 8, 7)
    _, fixed = raster._place_labels(points, FONTS)
    placements, laddered = raster._place_labels(points, ladder)
    assert laddered["dropped"] < fixed["dropped"]
    assert laddered["shrunk"] > 0
    assert _dots_covered(points, placements, ladder) == []


def test_shrinking_is_a_last_resort_not_a_packing_strategy():
    # Points in open ground must keep the full size even when a ladder exists.
    points = [(1000.0 + i * 80.0, 1000.0, str(2100 + 2 * i), "PELEE BLVD")
              for i in range(6)]
    placements, stats = raster._place_labels(points, fonts(11, 9, 8, 7))
    assert stats["shrunk"] == 0
    assert all(p[4] == 11 for p in placements)


def test_font_ladder_descends_from_the_zooms_size():
    cfg = Config(slug="testville", provider="Test")
    assert raster.font_ladder(cfg, 19) == (11, 9, 8, 7)
    # z17 already renders at 9px, so the ladder must not step back up to 11.
    assert raster.font_ladder(cfg, 17) == (9, 8, 7)


def test_label_font_size_is_per_zoom():
    cfg = Config(slug="testville", provider="Test")
    assert raster.label_font_size(cfg, 17) == 9      # engine default for z17
    assert raster.label_font_size(cfg, 18) == raster.FONT_SIZE
    assert raster.label_font_size(cfg, 19) == raster.FONT_SIZE


def test_a_city_can_override_the_per_zoom_font_size():
    cfg = Config(slug="testville", provider="Test")
    cfg.raster_font_sizes = {17: 10, 19: 12}
    assert raster.label_font_size(cfg, 17) == 10     # override beats the default
    assert raster.label_font_size(cfg, 18) == raster.FONT_SIZE
    assert raster.label_font_size(cfg, 19) == 12


def _placed_geometry(points, placements, ladder=None):
    """[(label box, leader segment or None)] for everything actually drawn."""
    ladder = ladder or FONTS
    out = []
    for i, placement in enumerate(placements):
        if placement is None or not points[i][2]:
            continue
        gx, gy, text, _st = points[i]
        anchor, dx, dy, leader, size = placement
        bb = raster._anchor_bbox(anchor, gx + dx, gy + dy,
                                 ladder[size].getlength(text), size)
        out.append((bb, raster._leader_endpoints(gx, gy, bb) if leader else None))
    return out


def test_segment_hits_box_ignores_the_empty_corners_of_its_bbox():
    # The whole point of the exact-geometry fix: a diagonal line's bounding box
    # is mostly empty, and the empty part must stay available.
    seg = ((0.0, 0.0), (20.0, 20.0))
    assert raster._segment_hits_box(seg, (8.0, 8.0, 12.0, 12.0))    # on the line
    assert not raster._segment_hits_box(seg, (14.0, 2.0, 18.0, 6.0))  # bbox only
    assert not raster._segment_hits_box(seg, (2.0, 14.0, 6.0, 18.0))  # other corner


def test_segments_cross_only_on_a_real_x():
    cross = ((0.0, 0.0), (10.0, 10.0)), ((0.0, 10.0), (10.0, 0.0))
    assert raster._segments_cross(*cross)
    # Two leaders leaving the same dot share an endpoint -- not a crossing.
    shared = ((0.0, 0.0), (10.0, 10.0)), ((0.0, 0.0), (10.0, -10.0))
    assert not raster._segments_cross(*shared)
    apart = ((0.0, 0.0), (10.0, 0.0)), ((0.0, 5.0), (10.0, 5.0))
    assert not raster._segments_cross(*apart)


def test_a_leader_never_crosses_a_label_or_another_leader():
    # The invariant the bbox reservation used to buy the lazy way.  A crowded
    # grid is where leaders proliferate, so it is the honest place to check.
    points = [(1000.0 + x * 11.0, 1000.0 + y * 11.0, str(100 + x + 10 * y), "GRID ST")
              for y in range(8) for x in range(8)]
    placements, stats = raster._place_labels(points, fonts(11, 9, 8, 7))
    assert stats["leadered"] > 0, "no leaders drawn -- the check would be vacuous"
    drawn = _placed_geometry(points, placements, fonts(11, 9, 8, 7))
    for i, (box, seg) in enumerate(drawn):
        if seg is None:
            continue
        for j, (other_box, other_seg) in enumerate(drawn):
            if i == j:
                continue
            assert not raster._segment_hits_box(seg, other_box), (i, j)
            if other_seg is not None:
                assert not raster._segments_cross(seg, other_seg), (i, j)


# Four doors of one building, close enough that three of the four labels need
# leaders.  This is 15-47 HAYS BLVD in miniature: reserving each leader's
# bounding box costs one of them its number even though the ground it wants is
# beside the line rather than on it.  Kept as coordinates rather than a
# generator because the case is a specific arrangement, not a general density --
# _bbox_leaders below is what makes it a regression test rather than a wish.
_LEADER_CLUSTER = [
    (1016.3, 1009.2, "10", "HAYS BLVD"),
    (1010.4, 1010.3, "11", "HAYS BLVD"),
    (1023.2, 1002.2, "12", "HAYS BLVD"),
    (1023.1, 1000.7, "13", "HAYS BLVD"),
]


class _bbox_leaders:
    """Context manager restoring the pre-fix rule: a leader reserves its bbox."""

    @staticmethod
    def _bbox(seg):
        (x0, y0), (x1, y1) = seg
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def __enter__(self):
        self._exact = (raster._segment_hits_box, raster._segments_cross)
        raster._segment_hits_box = lambda seg, box: raster._boxes_overlap(
            self._bbox(seg), box)
        raster._segments_cross = lambda a, b: raster._boxes_overlap(
            self._bbox(a), self._bbox(b))
        return self

    def __exit__(self, *exc):
        raster._segment_hits_box, raster._segments_cross = self._exact


def test_space_beside_a_leader_is_usable():
    ladder = fonts(11, 9, 8, 7)
    placements, stats = raster._place_labels(_LEADER_CLUSTER, ladder)
    assert stats["dropped"] == 0
    assert stats["leadered"] > 0, "no leaders -- the case would prove nothing"
    assert _dots_covered(_LEADER_CLUSTER, placements, ladder) == []


def test_reserving_the_leaders_bbox_is_what_used_to_lose_the_label():
    # Pins the cause, not just the symptom: flip the rule back and the same
    # four doors lose one.  If this ever stops failing, the fixture has gone
    # slack and test_space_beside_a_leader_is_usable is no longer proving
    # anything about leader geometry.
    ladder = fonts(11, 9, 8, 7)
    with _bbox_leaders():
        _, old = raster._place_labels(_LEADER_CLUSTER, ladder)
    _, new = raster._place_labels(_LEADER_CLUSTER, ladder)
    assert old["dropped"] > new["dropped"] == 0


def test_dense_cluster_drops_rather_than_overlapping():
    # When there is genuinely no room, dropping is the honest outcome -- what we
    # must never do is draw over a dot.
    points = [(1000.0 + i * 3.0, 1000.0, str(100 + i), "SOME ST") for i in range(30)]
    placements, stats = raster._place_labels(points, FONTS)
    assert stats["dropped"] > 0
    assert _dots_covered(points, placements) == []


def _slim(rows):
    """Write a throwaway slim GeoJSONL and return its path."""
    fd, path = tempfile.mkstemp(suffix=".geojsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for number, unit, street in rows:
            f.write(json.dumps({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-79.7, 43.45]},
                "properties": {"housenumber": number, "unit": unit, "street": street},
            }) + "\n")
    return path


def _read(rows, zoom=19):
    path = _slim(rows)
    try:
        points, complexes, _source_rows = raster._read_points(path, zoom)
        return points, complexes
    finally:
        os.remove(path)


def test_read_points_prefers_the_full_label_and_only_marks_candidates():
    # Reading never shortens: it hands back what every point would rather have,
    # plus a note of which points *could* be shortened if it comes to that.
    n = raster.MIN_COMPLEX_DOORS
    points, complexes = _read([("2441", str(u), "GREENWICH DR")
                               for u in range(1, n + 1)])
    key = raster.complex_key("GREENWICH DR", "2441")
    assert [p[2] for p in points] == [f"{u}-2441" for u in range(1, n + 1)]
    assert {p[3] for p in points} == {"GREENWICH DR"}
    assert complexes == [(key, str(u)) for u in range(1, n + 1)]
    assert raster.street_display(key) == "2441 GREENWICH DR"


def test_too_few_doors_is_not_a_complex_at_all():
    n = raster.MIN_COMPLEX_DOORS - 1
    _points, complexes = _read([("2441", str(u), "GREENWICH DR")
                                for u in range(1, n + 1)])
    assert complexes == [None] * n


def test_complexes_are_split_by_street_number_not_just_street():
    # Two towers on one street are two complexes; their units must not merge
    # into one colour group, or "12" would be ambiguous between them.
    rows = [("2441", str(u), "GREENWICH DR") for u in range(1, 5)]
    rows += [("2435", str(u), "GREENWICH DR") for u in range(1, 5)]
    _points, complexes = _read(rows)
    assert {c[0] for c in complexes} == {
        raster.complex_key("GREENWICH DR", "2441"),
        raster.complex_key("GREENWICH DR", "2435"),
    }


def test_unitless_addresses_are_never_complex_candidates():
    rows = [("2441", str(u), "GREENWICH DR") for u in range(1, 5)]
    rows += [("2454", "", "GREENWICH DR"), ("2452", "", "GREENWICH DR")]
    points, complexes = _read(rows)
    assert complexes[-2:] == [None, None]
    assert points[-1][2] == "2452" and points[-1][3] == "GREENWICH DR"


def _complex_points(n, spacing, key="GREENWICH DR", number="2441"):
    """A tight row of ``n`` doors of one complex, ``spacing`` px apart."""
    points = [(1000.0 + i * spacing, 1000.0, f"{i + 1}-{number}", key)
              for i in range(n)]
    complexes = [(raster.complex_key(key, number), str(i + 1)) for i in range(n)]
    return points, complexes


def test_a_roomy_complex_keeps_its_full_labels():
    # The whole point of deciding this at placement time: where the ordinary
    # labels fit, nothing changes -- full text, parent street, no anchor.
    points, complexes = _complex_points(5, spacing=90.0)
    placed, placements, stats, crowded = raster._place_complexes(
        points, complexes, FONTS
    )
    assert crowded == set()
    assert placed == points
    assert [p[2] for p in placed] == [f"{i + 1}-2441" for i in range(5)]
    assert stats["dropped"] == 0


def test_a_crowded_complex_falls_back_to_unit_numbers():
    points, complexes = _complex_points(14, spacing=13.0)
    key = raster.complex_key("GREENWICH DR", "2441")
    _, full_stats = raster._place_labels(points, FONTS)   # what full labels cost
    placed, placements, stats, crowded = raster._place_complexes(
        points, complexes, FONTS
    )
    assert crowded == {key}
    assert [p[2] for p in placed] == [str(i + 1) for i in range(14)]
    assert {p[3] for p in placed} == {key}          # own colour group
    assert stats["dropped"] < full_stats["dropped"]  # that is why we shortened
    assert _dots_covered(placed, placements) == []


def test_only_the_crowded_complex_is_shortened():
    # One tight complex and one roomy one, far apart: the roomy one must not be
    # dragged along by its neighbour.
    tight, tight_c = _complex_points(14, spacing=13.0, number="2441")
    roomy, roomy_c = _complex_points(5, spacing=90.0, number="2435")
    roomy = [(gx, gy + 4000.0, t, st) for gx, gy, t, st in roomy]
    placed, _pl, _stats, crowded = raster._place_complexes(
        tight + roomy, tight_c + roomy_c, FONTS
    )
    assert crowded == {raster.complex_key("GREENWICH DR", "2441")}
    assert [p[2] for p in placed[14:]] == [f"{i + 1}-2435" for i in range(5)]


def test_shorten_leaves_non_members_untouched():
    points, complexes = _complex_points(4, spacing=90.0)
    points.append((5000.0, 5000.0, "2454", "GREENWICH DR"))
    complexes.append(None)
    key = raster.complex_key("GREENWICH DR", "2441")
    out = raster._shorten(points, complexes, {key})
    assert out[-1] == points[-1]
    assert out[0] == (1000.0, 1000.0, "1", key)


def test_complex_key_round_trips_to_a_display_name():
    key = raster.complex_key("GREENWICH DR", "2441")
    assert raster.street_display(key) == "2441 GREENWICH DR"
    # A plain street is not a complex and passes through untouched.
    assert raster.street_display("GREENWICH DR") == "GREENWICH DR"


def test_a_complex_never_takes_its_parent_streets_colour():
    # The point of giving a complex its own colour is that its bare unit numbers
    # read as belonging to it and not to the road running past.
    for street in ("GREENWICH DR", "POST RD", "HAYS BLVD", "MUNN'S AVE",
                   "SPEERS RD", "EAST ST", "MARINE DR", "SIXTH LINE"):
        for number in ("15", "2441", "2480", "51", "1272", "76", "2314"):
            key = raster.complex_key(street, number)
            assert raster._street_index(key) != raster._street_index(street), key


def test_a_street_starting_with_a_digit_is_not_mistaken_for_a_complex():
    # "16 MILE DR" is a road, not 16 of MILE DR -- the NUL-separated key is what
    # distinguishes them, so name sniffing must not creep back in.
    assert raster.street_display("16 MILE DR") == "16 MILE DR"
    assert raster._street_index("16 MILE DR") == raster._hash_index("16 MILE DR")


def test_a_streets_colour_is_stable_across_tiles():
    # Palette assignment must stay a pure function of the key or a street's
    # colour flickers from tile to tile.
    a = raster._assign_tile_colors({"GREENWICH DR", "POST RD"})
    b = raster._assign_tile_colors({"GREENWICH DR", "HAYS BLVD", "SIXTH LINE"})
    assert a["GREENWICH DR"] == b["GREENWICH DR"]
    key = raster.complex_key("GREENWICH DR", "2441")
    c = raster._assign_tile_colors({key})
    d = raster._assign_tile_colors({key, "GREENWICH DR", "POST RD"})
    assert c[key] == d[key]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("All raster placement tests passed.")
