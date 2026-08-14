"""Marker geometry for the zoom that finishes the labels.

A handful of addresses cannot be labelled even at the deepest raster zoom -- in
Oakville, doors of one apartment block a metre or so apart -- so the build adds
a deeper zoom until none are left (``[layer].raster_complete_to``).

Those zooms share the layer's URL and advertised range, but they are *sparse*:
only the tiles holding the stragglers are rendered.  Everywhere else the zoom is
absent and the client gets a 404, which is the deliberate trade -- rendering one
whole is three orders of magnitude more expensive (Toronto: 283,239 tiles /
666 MB against 180) and adds nothing the parent zoom was not already showing.
How a client takes that 404 varies: Leaflet marks the failed tile active and
prunes the parent it was scaling up (``_tileReady`` -> ``_pruneTiles``), so it
blanks, which is why the landing page's preview stops at the deepest whole zoom
and upscales instead of fetching into the sparse one.

The marker is what makes a sparse zoom navigable: the zoom above draws a dashed
box around the ground whose numbers only appear deeper -- "there is more here
than fits, and it is here that zooming in pays".  The boxes are drawn as the
outline of their **union**, because where an apartment block needs a deeper zoom
several neighbouring tiles qualify at once, and one box per tile degenerates
into a lattice across the parent that reads as debug output rather than a hint.

Everything here is pure geometry over tile coordinates; rendering lives in
raster.py.
"""

from collections import defaultdict

from .tilemath import TILE_SIZE

# Dash pattern for the marker, in parent-zoom pixels.
DASH_ON = 7
DASH_OFF = 5
# Keep the outline just inside the ground it marks, so two adjacent regions
# never draw over each other's edge.
INSET = 1.5


def cells_for(points, indices, base_zoom, deep_zoom):
    """{(tx, ty): [index, ...]} -- the deep-zoom tiles holding those points.

    ``points`` are in *base zoom* pixels (the zoom the caller placed at); the
    deep tile is found by scaling, so the caller does not need a second read of
    the source at the deeper zoom just to bucket.
    """
    scale = 2 ** (deep_zoom - base_zoom)
    cells = defaultdict(list)
    for i in indices:
        gx, gy = points[i][0] * scale, points[i][1] * scale
        cells[(int(gx // TILE_SIZE), int(gy // TILE_SIZE))].append(i)
    return dict(cells)


def parent_cells(cells, levels=1):
    """The tiles ``levels`` zooms up that contain these tiles."""
    step = 2 ** levels
    return {(tx // step, ty // step) for tx, ty in cells}


def boundary_segments(cells, deep_zoom, parent_zoom):
    """Outline of the union of ``cells``, in parent-zoom pixel coordinates.

    A side is kept only where the neighbouring tile is not itself a detail
    tile, which is what turns a block of adjacent tiles into one box.
    """
    scale = 2 ** (deep_zoom - parent_zoom)
    quad = TILE_SIZE / scale          # a detail tile, in parent-zoom pixels
    cells = set(cells)
    segments = []
    for tx, ty in cells:
        x0, y0 = tx * quad, ty * quad
        x1, y1 = x0 + quad, y0 + quad
        if (tx, ty - 1) not in cells:
            segments.append((x0, y0 + INSET, x1, y0 + INSET))
        if (tx, ty + 1) not in cells:
            segments.append((x0, y1 - INSET, x1, y1 - INSET))
        if (tx - 1, ty) not in cells:
            segments.append((x0 + INSET, y0, x0 + INSET, y1))
        if (tx + 1, ty) not in cells:
            segments.append((x1 - INSET, y0, x1 - INSET, y1))
    return segments


def segments_by_tile(segments):
    """{(tx, ty): [(x0, y0, x1, y1), ...]} in tile-local pixels.

    A union outline can run along a tile seam, so a segment may belong to two
    tiles; each gets its own copy in local coordinates.
    """
    out = defaultdict(list)
    for sx0, sy0, sx1, sy1 in segments:
        for tx in _tiles_spanned(sx0, sx1):
            for ty in _tiles_spanned(sy0, sy1):
                out[(tx, ty)].append((sx0 - tx * TILE_SIZE, sy0 - ty * TILE_SIZE,
                                      sx1 - tx * TILE_SIZE, sy1 - ty * TILE_SIZE))
    return dict(out)


def _tiles_spanned(a, b):
    """Tile indices a segment's extent covers, on the half-open convention.

    A box drawn around the last quadrant of a tile ends exactly on that tile's
    edge.  Counting the neighbour there would hand it a segment of zero visible
    length -- and, since a tile is written for every tile a marker touches, a
    whole PNG holding one invisible hairline.
    """
    lo, hi = (a, b) if a <= b else (b, a)
    first, last = int(lo // TILE_SIZE), int(hi // TILE_SIZE)
    if last > first and hi == last * TILE_SIZE:
        last -= 1
    return range(first, last + 1)


def markers_for(cells, deep_zoom, parent_zoom):
    """The marker segments a parent zoom must draw, keyed by parent tile."""
    return segments_by_tile(boundary_segments(cells, deep_zoom, parent_zoom))


def dashes(segment):
    """Split one segment into the dashes that draw it.

    Yielded as whole segments so the caller can stroke them twice -- a wide
    halo pass under a thin coloured pass -- and have the two line up.
    """
    x0, y0, x1, y1 = segment
    length = max(abs(x1 - x0), abs(y1 - y0))
    if not length:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    t = 0.0
    while t < length:
        end = min(t + DASH_ON, length)
        yield (x0 + ux * t, y0 + uy * t, x0 + ux * end, y0 + uy * end)
        t = end + DASH_OFF
