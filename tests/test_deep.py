"""Tests for the marker geometry (see deep.py) and the completion zoom range.

The rule these pin is the one the first prototype got wrong: a box per tile
turns a block of neighbouring tiles into a lattice across the parent, so the
marker has to be the outline of the *union*.
"""

import os
import tempfile

from addresslayerist import deep
from addresslayerist.config import Config
from addresslayerist.tilemath import TILE_SIZE


def test_cells_for_scales_points_into_deeper_tiles():
    # One point in the middle of tile (10, 20) at the base zoom lands in the
    # middle of that tile's four children one zoom down.
    points = [(10.5 * TILE_SIZE, 20.5 * TILE_SIZE, "1", "SOME ST")]
    cells = deep.cells_for(points, [0], base_zoom=19, deep_zoom=20)
    assert cells == {(21, 41): [0]}
    assert deep.parent_cells(cells) == {(10, 20)}
    # Two zooms deeper is the same point, four times the tile index.
    assert set(deep.cells_for(points, [0], 19, 21)) == {(42, 82)}


def test_cells_group_points_that_share_a_tile():
    points = [(100.0, 100.0, "1", "S"), (104.0, 100.0, "2", "S"),
              (5000.0, 5000.0, "3", "S")]
    cells = deep.cells_for(points, [0, 1, 2], 19, 20)
    assert sorted(map(len, cells.values())) == [1, 2]


def test_one_cell_is_boxed_on_all_four_sides():
    segments = deep.boundary_segments({(2, 2)}, deep_zoom=20, parent_zoom=19)
    assert len(segments) == 4
    quad = TILE_SIZE / 2
    # The box sits on the ground that tile covers, in parent-zoom pixels.
    xs = [x for seg in segments for x in (seg[0], seg[2])]
    ys = [y for seg in segments for y in (seg[1], seg[3])]
    assert min(xs) >= 2 * quad and max(xs) <= 3 * quad
    assert min(ys) >= 2 * quad and max(ys) <= 3 * quad


def test_neighbouring_cells_merge_into_one_outline():
    # The lattice bug: four boxes drawn separately give 16 edges and a cross
    # through the middle of the parent; the union gives 8 and no cross.
    block = {(2, 2), (3, 2), (2, 3), (3, 3)}
    segments = deep.boundary_segments(block, 20, 19)
    assert len(segments) == 8
    # Nothing is drawn on the shared interior seams.
    quad = TILE_SIZE / 2
    seam = 3 * quad
    for x0, y0, x1, y1 in segments:
        assert not (x0 == x1 == seam), "vertical seam should not be drawn"
        assert not (y0 == y1 == seam), "horizontal seam should not be drawn"


def test_an_l_shape_keeps_its_inner_corner():
    segments = deep.boundary_segments({(0, 0), (1, 0), (0, 1)}, 20, 19)
    assert len(segments) == 8      # 12 sides minus 2 shared pairs


def test_a_seam_segment_is_given_to_both_tiles_it_touches():
    # A union outline can run along a parent tile boundary; both neighbours draw
    # their half, in their own local coordinates.
    segment = (TILE_SIZE - 10.0, TILE_SIZE / 2, TILE_SIZE + 10.0, TILE_SIZE / 2)
    by_tile = deep.segments_by_tile([segment])
    assert set(by_tile) == {(0, 0), (1, 0)}
    assert by_tile[(0, 0)][0][0] == TILE_SIZE - 10.0
    assert by_tile[(1, 0)][0][0] == -10.0        # continues in from the left


def test_markers_land_in_the_parent_tile_holding_that_ground():
    cells = {(21, 41)}
    markers = deep.markers_for(cells, deep_zoom=20, parent_zoom=19)
    assert set(markers) == {(10, 20)}
    for x0, y0, x1, y1 in markers[(10, 20)]:
        assert 0 <= x0 <= TILE_SIZE and 0 <= y0 <= TILE_SIZE
        assert 0 <= x1 <= TILE_SIZE and 0 <= y1 <= TILE_SIZE


def test_dashes_stay_inside_the_segment_they_draw():
    segment = (10.0, 5.0, 60.0, 5.0)
    parts = list(deep.dashes(segment))
    assert parts, "a segment longer than one dash must produce dashes"
    assert all(y0 == y1 == 5.0 for _x0, y0, _x1, y1 in parts)
    assert min(p[0] for p in parts) >= 10.0
    assert max(p[2] for p in parts) <= 60.0
    # Gaps are what make it read as a marker rather than a border.
    drawn = sum(p[2] - p[0] for p in parts)
    assert drawn < 50.0


def test_a_zero_length_segment_draws_nothing():
    assert list(deep.dashes((5.0, 5.0, 5.0, 5.0))) == []


def test_completion_zooms_start_past_the_configured_raster_max():
    cfg = Config(slug="testville", provider="Test")
    assert cfg.completion_zooms == []           # off by default
    cfg.raster_complete_to = 21
    assert cfg.completion_zooms == [20, 21]
    cfg.raster_complete_to = 19                 # not deeper than we already go
    assert cfg.completion_zooms == []


def test_the_advertised_range_is_what_was_built_not_what_was_asked_for():
    # A completion zoom is only rendered when the deepest configured zoom left
    # something unlabelled, so the site and the ELI entry have to read the
    # tiles rather than the config -- advertising a zoom with no tiles would
    # send editors to 404s, and 404s draw blank.
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(slug="testville", provider="Test", project_dir=tmp)
        cfg.raster_complete_to = 21
        assert cfg.built_raster_zooms == [16, 17, 18, 19]    # nothing built yet
        for zoom in (16, 17, 18, 19, 20):
            os.makedirs(os.path.join(cfg.raster_tile_dir, str(zoom)))
        os.makedirs(os.path.join(cfg.raster_tile_dir, "notazoom"))
        assert cfg.built_raster_zooms == [16, 17, 18, 19, 20]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("All detail-layer geometry tests passed.")
