"""Tests for tile pruning (see raster._prune_tiles / _prune_zooms).

The rule these pin is the one the build got wrong for its whole life: rendering
writes into the published tree, and ``publish`` snapshots whatever is on disk,
so a run that renders *fewer* tiles than the last one has to say so on disk or
the surplus goes out again.  The case that made it matter is a completion zoom
turning sparse -- Oakville's z20 went from 45,500 tiles to ~36 and kept
publishing the rest.
"""

import os
import tempfile

from PIL import ImageFont

from addresslayerist import raster
from addresslayerist.config import ENGINE_ASSETS_DIR, Config
from addresslayerist.tilemath import TILE_SIZE


def _fonts(*sizes):
    path = os.path.join(ENGINE_ASSETS_DIR, "font", "DejaVuSans.ttf")
    return {s: ImageFont.truetype(path, s) for s in (sizes or (11,))}


def _written(out_dir):
    """{(tx, ty)} for the PNGs under a rendered zoom directory."""
    return {(int(x), int(os.path.splitext(f)[0]))
            for x in os.listdir(out_dir)
            for f in os.listdir(os.path.join(out_dir, x))}


def _points_in_tiles(*tiles):
    """One labelled point in the middle of each named tile."""
    return [((tx + 0.5) * TILE_SIZE, (ty + 0.5) * TILE_SIZE, "1", "SOME ST")
            for tx, ty in tiles]


def _touch(out_dir, x, y, name=None):
    """Put a file where a tile would go, without rendering one."""
    column = os.path.join(out_dir, str(x))
    os.makedirs(column, exist_ok=True)
    path = os.path.join(column, name or f"{y}.png")
    with open(path, "wb") as fh:
        fh.write(b"stale")
    return path


def _render(out_dir, tiles, only=None, markers=None):
    fonts = _fonts()
    points = _points_in_tiles(*tiles)
    placements, _stats = raster._place_labels(points, fonts)
    return raster._render_tiles(points, placements, fonts, _fonts(12)[12],
                                draw_street=True, markers=markers or {},
                                out_dir=out_dir, only=only)


def test_a_zoom_that_turns_sparse_drops_the_tiles_it_no_longer_ships():
    # The Oakville z20 case in miniature: rendered whole once, then rendered as
    # a completion zoom over the one tile that still has something to add.
    with tempfile.TemporaryDirectory() as tmp:
        _render(tmp, [(10, 20), (11, 20), (99, 99)])
        assert _written(tmp) == {(10, 20), (11, 20), (99, 99)}
        _render(tmp, [(10, 20), (11, 20), (99, 99)], only={(11, 20): [1]})
        assert _written(tmp) == {(11, 20)}
        # A column emptied by the prune goes with it, so the tree does not fill
        # up with directories holding nothing.
        assert sorted(os.listdir(tmp)) == ["11"]


def test_a_re_render_of_the_same_tiles_deletes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        _render(tmp, [(10, 20), (11, 20)])
        assert raster._prune_tiles(tmp, {(10, 20), (11, 20)}) == 0
        assert _written(tmp) == {(10, 20), (11, 20)}


def test_a_marker_only_tile_survives_the_prune():
    # _render_tiles writes tiles that hold nothing but a marker; the prune runs
    # off the same key set, so it must not undo them.
    from addresslayerist import deep

    with tempfile.TemporaryDirectory() as tmp:
        _touch(tmp, 42, 42)
        markers = deep.markers_for({(84, 84)}, deep_zoom=21, parent_zoom=20)
        assert set(markers) == {(42, 42)}, "the marker lands outside our own tile"
        _render(tmp, [(10, 20)], only={(10, 20): [0]}, markers=markers)
        assert _written(tmp) == {(10, 20), (42, 42)}


def test_the_prune_leaves_files_that_are_not_tiles_alone():
    # Only <x>/<y>.png is ours. Anything else is somebody's, and is also enough
    # to keep its column from being removed.
    with tempfile.TemporaryDirectory() as tmp:
        _touch(tmp, 10, 20, name="README.txt")
        _touch(tmp, 10, 21)
        os.makedirs(os.path.join(tmp, "notazoomcolumn"), exist_ok=True)
        assert raster._prune_tiles(tmp, set()) == 1
        assert sorted(os.listdir(tmp)) == ["10", "notazoomcolumn"]
        assert os.listdir(os.path.join(tmp, "10")) == ["README.txt"]


def test_prune_tiles_is_quiet_about_a_zoom_that_was_never_rendered():
    with tempfile.TemporaryDirectory() as tmp:
        assert raster._prune_tiles(os.path.join(tmp, "20"), {(1, 1)}) == 0


def _cfg(tmp):
    return Config(slug="testville", provider="Test", project_dir=tmp)


def test_a_zoom_the_build_no_longer_renders_is_dropped_whole():
    # built_raster_zooms reads the range off the disk, so a leftover z20 does
    # not just waste bytes -- the site, the JOSM snippet and the ELI entry go
    # on advertising a zoom that is no longer built.
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        for zoom in (18, 19, 20):
            _touch(os.path.join(cfg.raster_tile_dir, str(zoom)), 1, 1)
        assert cfg.built_raster_zooms == [18, 19, 20]
        assert raster._prune_zooms(cfg, {18, 19}) == 1
        assert cfg.built_raster_zooms == [18, 19]


def test_prune_zooms_ignores_anything_that_is_not_a_zoom():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        _touch(os.path.join(cfg.raster_tile_dir, "19"), 1, 1)
        os.makedirs(os.path.join(cfg.raster_tile_dir, "scratch"), exist_ok=True)
        with open(os.path.join(cfg.raster_tile_dir, "notes.txt"), "w") as fh:
            fh.write("keep me")
        assert raster._prune_zooms(cfg, {19}) == 0
        assert sorted(os.listdir(cfg.raster_tile_dir)) == ["19", "notes.txt",
                                                          "scratch"]
