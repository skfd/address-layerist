"""Render address points into labelled raster (PNG) map tiles using Pillow.

Pure Python -- no native dependencies, runs the same on Windows and Linux.

Label placement is greedy with 20 candidate slots per point, in priority order:
  1) 4 inner slots hugging the dot (right, left, above, below) -- no leader.
  2) Outer ring at radius LEADER_R1, 8 compass directions -- with a leader line.
  3) Outer ring at radius LEADER_R2, same 8 directions -- with a leader line.
A leader is a thin line from the dot edge to the label-box edge.  Leaders take
part in the collision check as the *segments* they are, so a leader will not
cross another label or another leader.  Reserving their bounding box instead --
which is what this did until the exact-geometry fix -- costs far more space than
it sounds: a diagonal leader's bbox is mostly empty, and for 16-47 HAYS BLVD it
reserved 329px2 against 154px2 of ink, which is what left its neighbour "15"
unlabelled with visible white space beside it.  Oakville went from 96 unlabelled
at z19 to 57, and from 5 to 0 at z20, by measuring the line rather than the
rectangle around it.  If even the outer rings collide, the label is dropped.

Every dot is seeded into the collision grid before placement starts, so a label
may never be drawn over *another* address's dot (skfd/oakville-address-layer#2).
Without that, a row of townhouses 30px apart would each take the inner-right
slot and each 30px-wide label would bury the next dot along -- which both reads
as that dot's own number and strands it: all 16 leadered candidates begin at the
dot, so once it is covered every fallback slot collides and the label is
dropped.  A point never blocks itself, hence the owner tag on grid entries.

Placement is run once globally per zoom so seam-straddling labels render in the
same slot in every tile they touch.

A label that fits nowhere at the zoom's font size is retried at FONT_SIZE_LADDER
sizes before being dropped, so shrinking only ever happens where the
alternative is no label at all.

The deepest zoom additionally runs the hidden-address check (audit.py) and
writes build/<slug>-hidden-z<zoom>.csv: dots that overlap another address's dot
and labels that were dropped there are hidden in the whole raster layer, since
clients upscale past the deepest zoom rather than fetching a deeper tile.

Street identity is conveyed by colour: hash(street_name) -> stable hue.  Address
labels and their leader lines use that colour.  In every tile, each
street that has at least STREET_LABEL_MIN_DOTS dots gets one floating street
name label placed in free space, in the same colour, so the dot and the name
are visually linked even when several streets meet at a corner.

An apartment/townhouse complex can become a street of its own: its doors are
then labelled with the bare unit number and coloured as a group, with the
floating label spelling out "2441 GREENWICH DR" beside them.  That only happens
where the ordinary "1-2441 2-2441 ..." labels do not fit -- a courtyard with
room for the full labels keeps them, since a bare "3" costs the reader an
indirection through the anchor label.  See _place_complexes.
"""

import hashlib
import json
import math
import os
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

from . import audit, deep
from .label import display_label
from .tilemath import TILE_SIZE, lonlat_to_pixel

DOT_COLOR = (224, 33, 138, 255)        # magenta -- visible over aerial imagery
DOT_RADIUS = 2
LABEL_COLOR = (0, 0, 0, 255)           # fallback when a point has no street
HALO_COLOR = (255, 255, 255, 235)
FONT_SIZE = 11                         # default label size, in pixels
# Per-zoom overrides.  A label's footprint does not shrink with the zoom, but
# the gap between dots does: at z17 a townhouse row is ~7px apart while an
# 11px label is ~30px wide, so most of the row goes unlabelled.  Dropping to 9px
# there labels more points than the pre-#2 build managed *while* keeping every
# dot clear.  8px was tried and rejected -- the halo starts eating the glyphs.
# A city can override this in layer.toml ([layer] raster_font_sizes).
FONT_SIZE_BY_ZOOM = {17: 9}
# Fallback sizes for a point that cannot place at the zoom's size.  Shrinking is
# tried only after every slot at the larger size has failed, so a label in open
# ground is never shrunk -- only the ones that would otherwise be dropped.  7px
# is the floor: below that the halo starts eating the glyphs.
FONT_SIZE_LADDER = (9, 8, 7)
STROKE_WIDTH = 1                       # white halo width, in pixels
LABEL_GAP = 2                          # gap between the dot and the inner label
LEADER_WIDTH = 1
LEADER_R1 = 16.0                       # first fallback ring radius (px)
LEADER_R2 = 28.0                       # second fallback ring radius (px)

# Marker: a dashed box around ground holding addresses this zoom cannot label
# but the next one down can -- "there is more here than fits, zoom in".  Same
# magenta as the dots, so it reads as this layer's own signal rather than as
# something in the imagery underneath.  See deep.py.
MARKER_COLOR = DOT_COLOR
MARKER_HALO = (255, 255, 255, 220)
MARKER_WIDTH = 1
MARKER_HALO_WIDTH = 3
# How many rescued addresses to name in the build log before summarising.
_COMPLETION_REPORT_LIMIT = 12

# Floating street-name labels (per tile, per street).
STREET_FONT_SIZE = 12
STREET_LABEL_MIN_DOTS = 2              # don't label streets with a lone dot
STREET_TILE_MARGIN = 2                 # keep the label fully inside the tile

# Accessibility-vetted palette.  Each entry passes WCAG AA (>=4.5:1 contrast)
# against the white halo, and the set is distinguishable under common forms
# of colour vision deficiency (no red/green or blue/indigo collisions).
# Vivid Material Design tones at L700/L800 -- bright but readable as text.
_PALETTE = (
    (194,  24,  91, 255),   # pink
    (123,  31, 162, 255),   # purple
    ( 25, 118, 210, 255),   # blue
    (  0, 121, 107, 255),   # teal
    ( 46, 125,  50, 255),   # green
    ( 93,  64,  55, 255),   # brown
    (211,  47,  47, 255),   # red
)
_FALLBACK_COLOR = (66, 66, 66, 255)    # neutral grey when street is missing

_SQRT_HALF = math.sqrt(0.5)
# 8 compass directions for outer rings: E, W, N, S, NE, SE, NW, SW.
_RING_DIRS = (
    (1.0, 0.0), (-1.0, 0.0), (0.0, -1.0), (0.0, 1.0),
    (_SQRT_HALF, -_SQRT_HALF), (_SQRT_HALF, _SQRT_HALF),
    (-_SQRT_HALF, -_SQRT_HALF), (-_SQRT_HALF, _SQRT_HALF),
)

# Spatial-hash cell size for collision lookups.
_GRID_CELL = 64
# Owner tag for grid entries that belong to no single point (i.e. label boxes).
_NO_OWNER = -1

# An apartment/townhouse complex is treated as its own street: this many doors
# sharing one street number on one street.  Below it, the group is just a couple
# of units and the full "3-2280" label is clearer than a bare "3".
MIN_COMPLEX_DOORS = 4
# Shortening a complex frees space, which can let a neighbouring complex that
# was crowded fit after all -- so placement is repeated until no new complex
# comes up short.  Oakville settles in 2-3 passes; the cap is a safety net.
MAX_COMPLEX_PASSES = 6
# Joins street and number into a complex key.  A NUL cannot occur in a street
# name, so the two halves are always recoverable -- which beats sniffing for a
# leading number and mis-firing on real streets like "16 MILE DR".
_COMPLEX_SEP = "\x00"

_index_cache = {}


def complex_key(street, number):
    """Grouping key for one complex, e.g. 2441 GREENWICH DR."""
    return f"{street}{_COMPLEX_SEP}{number}"


def street_display(key):
    """The name to draw for a grouping key ("2441 GREENWICH DR")."""
    street, sep, number = key.partition(_COMPLEX_SEP)
    return f"{number} {street}" if sep else street


def _hash_index(name):
    h = int.from_bytes(hashlib.md5(name.encode("utf-8")).digest()[:4], "big")
    return h % len(_PALETTE)


def _street_index(key):
    """Stable preferred palette index for a street (or complex) key.

    A complex must not land on its parent street's colour -- the whole point of
    giving it one is that its unit numbers read as belonging to the complex and
    not to the road running past it.  On a clash the complex shifts one slot
    along.  That stays a pure function of the key, so the colour cannot flicker
    across a tile seam the way per-tile collision resolution used to.
    """
    cached = _index_cache.get(key)
    if cached is not None:
        return cached
    idx = _hash_index(key)
    street, sep, _number = key.partition(_COMPLEX_SEP)
    if sep and idx == _hash_index(street):
        idx = (idx + 1) % len(_PALETTE)
    _index_cache[key] = idx
    return idx


def _assign_tile_colors(streets):
    """Map each street to its stable palette colour.

    The colour is a pure function of the street name (hash -> palette slot),
    so a street keeps the same colour in every tile it appears in.  Earlier
    versions resolved same-slot collisions per tile by shifting the loser to
    the next free slot, but that made a street's colour flip across a tile
    seam whenever it collided in one tile and not the neighbour.  Two streets
    sharing a colour is preferable to that flicker.
    """
    return {st: _PALETTE[_street_index(st)] for st in streets if st}


def _color_of(color_map, name):
    """Look up the tile-local colour for a street, with grey fallback."""
    if not name:
        return _FALLBACK_COLOR
    return color_map.get(name, _FALLBACK_COLOR)


def build_raster(cfg):
    """Render PNG tiles for every zoom in cfg.raster_zooms, then any completion
    zooms needed to leave no address unlabelled (see cfg.raster_complete_to).

    Returns a dict {zoom: tile_count}.
    """
    slim_path = cfg.slim_path
    if not os.path.isfile(slim_path):
        raise RuntimeError(f"Slim GeoJSONL not found: {slim_path}. Run 'slim' first.")
    street_font = ImageFont.truetype(
        cfg.asset(os.path.join("font", "DejaVuSans-Bold.ttf")), STREET_FONT_SIZE
    )
    label_zooms = set(cfg.raster_label_zooms)
    fonts = {z: _fonts_for(cfg, z) for z in cfg.raster_zooms}
    # The hidden-address check only means something at the deepest zoom: that is
    # the last tile a client fetches, so what is unreadable there is unreadable
    # everywhere.  See audit.py.
    deepest = max(cfg.raster_zooms, default=None)
    counts = {z: _render_zoom(cfg, slim_path, z, fonts[z], street_font, label_zooms)
              for z in cfg.raster_zooms if z != deepest}
    if deepest is None:
        return counts

    # The deepest zoom is rendered last and by hand, because what it draws
    # depends on what the detail layer will rescue: the marker boxes have to be
    # known before the tile is drawn, or the street-name placer cannot avoid
    # them.
    labelled = deepest in label_zooms
    points, placements, rows = _place_zoom(cfg, slim_path, deepest, fonts[deepest],
                                           labelled)
    audit.report(cfg, deepest, rows, points, placements, labelled,
                 stack_distance=2.0 * DOT_RADIUS)
    levels = _completion_levels(cfg, slim_path, points, placements)
    markers = (deep.markers_for(levels[0].cells, levels[0].zoom, deepest)
               if levels else {})
    out_dir = os.path.join(cfg.raster_tile_dir, str(deepest))
    print(f"Raster z{deepest}: rendering tiles (labels={labelled}) ...")
    counts[deepest] = _render_tiles(points, placements, fonts[deepest], street_font,
                                    labelled, markers, out_dir)
    counts.update(_render_completion(cfg, levels, street_font, rows))
    return counts


def _fonts_for(cfg, zoom):
    label_path = cfg.asset(os.path.join("font", "DejaVuSans.ttf"))
    return {s: ImageFont.truetype(label_path, s) for s in font_ladder(cfg, zoom)}


class _Level:
    """One completion zoom: what it draws, and which tiles hold the stragglers."""

    def __init__(self, zoom, cells, points, placements, fonts, rescued):
        self.zoom = zoom
        self.cells = cells              # {(tx, ty): [address index, ...]}
        self.points = points
        self.placements = placements
        self.fonts = fonts
        self.rescued = rescued          # indices this zoom labels for the first time


def _completion_levels(cfg, slim_path, base_points, base_placements):
    """Plan the zooms needed to finish labelling, one _Level each.

    A completion zoom ships only the tiles that hold the addresses it exists to
    rescue: ``cells`` records those tiles, and they are the whole of what is
    rendered.  Everywhere else the zoom is simply absent, and a client that asks
    there gets a 404 -- accepted deliberately, because rendering the zoom whole
    costs three orders of magnitude more (Toronto: 283,239 tiles / 666 MB
    against 180 tiles) to say nothing new about ground the parent already
    covers.  The dashed marker box on the parent is what makes the sparseness
    navigable: it says where zooming in has something to add.

    Trailing levels that rescue nobody are dropped -- that is the signature of
    two doors on one coordinate, which no zoom parts, and rendering a whole
    extra zoom of the city for them would buy nothing at all.
    """
    zooms = cfg.completion_zooms
    pending = [i for i, p in enumerate(base_placements)
               if p is None and base_points[i][2]]
    if not zooms or not pending:
        return []

    levels = []
    for zoom in zooms:
        if not pending:
            break
        fonts = _fonts_for(cfg, zoom)
        print(f"Raster z{zoom}: placing for {len(pending):,} unlabelled "
              f"address{'' if len(pending) == 1 else 'es'} ...")
        points, complexes, _rows = _read_points(slim_path, zoom)
        points, placements, _stats, _crowded = _place_complexes(
            points, complexes, fonts)
        # Reading again at this zoom keeps index i the same address -- the slim
        # file is read in order -- so the pending list carries across zooms.
        cells = deep.cells_for(points, pending, zoom, zoom)
        rescued = [i for i in pending if placements[i] is not None]
        levels.append(_Level(zoom, cells, points, placements, fonts, rescued))
        pending = [i for i in pending if placements[i] is None]
    while levels and not levels[-1].rescued:
        levels.pop()
    return levels


def _render_completion(cfg, levels, street_font, rows):
    """Render each completion zoom's own tiles; return {zoom: tile_count}.

    Only ``level.cells`` is written -- the tiles holding the addresses this zoom
    rescues -- plus whatever tiles a marker for the *next* level runs through.
    """
    counts = {}
    for n, level in enumerate(levels):
        # A level marks the level below it, so a mapper who follows one box
        # finds the next one waiting rather than a dead end.
        markers = {}
        if n + 1 < len(levels):
            deeper = levels[n + 1]
            markers = deep.markers_for(deeper.cells, deeper.zoom, level.zoom)
        out_dir = os.path.join(cfg.raster_tile_dir, str(level.zoom))
        planned = len(set(level.cells) | set(markers))
        print(f"Raster z{level.zoom}: rendering {planned:,} tiles for "
              f"{len(level.rescued):,} addresses the zoom above cannot show ...")
        counts[level.zoom] = _render_tiles(
            level.points, level.placements, level.fonts, street_font,
            draw_street=True, markers=markers, out_dir=out_dir,
            only=level.cells,
        )
        for i in level.rescued[:_COMPLETION_REPORT_LIMIT]:
            _lon, _lat, number, unit, street = rows[i]
            print(f"Raster z{level.zoom}:   {unit + '-' if unit else ''}"
                  f"{number} {street}")
        extra = len(level.rescued) - _COMPLETION_REPORT_LIMIT
        if extra > 0:
            print(f"Raster z{level.zoom}:   ... and {extra:,} more")
    return counts


def _place_zoom(cfg, slim_path, zoom, fonts, labelled):
    """Read the slim file at one zoom and place its labels.

    Returns (points, placements, rows); ``points`` is the list the placements
    refer to, which is what a caller must render (see _place_complexes).
    """
    print(f"Raster z{zoom}: reading points ...")
    points, complexes, rows = _read_points(slim_path, zoom)
    if not labelled:
        return points, [None] * len(points), rows

    print(f"Raster z{zoom}: placing {len(points):,} labels ...")
    points, placements, stats, crowded = _place_complexes(points, complexes, fonts)
    print(f"Raster z{zoom}: placed inner={stats['inner']:,} "
          f"leadered={stats['leadered']:,} dropped={stats['dropped']:,} "
          f"(shrunk={stats['shrunk']:,}) of {len(points):,}")
    if crowded:
        short = sum(1 for c in complexes if c and c[0] in crowded)
        print(f"Raster z{zoom}: {len(crowded):,} crowded complexes drawn as "
              f"unit numbers ({short:,} addresses)")
    return points, placements, rows


def _render_tiles(points, placements, fonts, street_font, draw_street, markers,
                  out_dir, only=None):
    """Bucket placed points into tiles and write the PNGs; return the count.

    Tiles that hold nothing but a marker are still written: a union outline can
    run along a seam into a tile with no addresses of its own, and half a box is
    worse than none.  ``only`` restricts writing to a set of tile keys, which is
    how a completion zoom ships just the ground it was added for; the base zooms
    pass nothing and are rendered whole.
    """
    tiles = defaultdict(list)
    for (gx, gy, text, st), placement in zip(points, placements):
        _bucket_point(tiles, gx, gy, text, st, placement, fonts)

    keys = (set(tiles) if only is None else set(only)) | set(markers)
    made_dirs = set()
    for key in keys:
        img = _render_tile(tiles.get(key, ()), fonts, street_font,
                           draw_street=draw_street, markers=markers.get(key, ()))
        tdir = os.path.join(out_dir, str(key[0]))
        if tdir not in made_dirs:
            os.makedirs(tdir, exist_ok=True)
            made_dirs.add(tdir)
        img.save(os.path.join(tdir, f"{key[1]}.png"), optimize=True)
    return len(keys)


def label_font_size(cfg, zoom):
    """Label size for a zoom: city override, else engine default for that zoom."""
    override = cfg.raster_font_sizes.get(zoom)
    if override:
        return override
    return FONT_SIZE_BY_ZOOM.get(zoom, FONT_SIZE)


def font_ladder(cfg, zoom):
    """Sizes to try for a zoom, largest first: the zoom's size, then fallbacks."""
    base = label_font_size(cfg, zoom)
    return (base, *(s for s in FONT_SIZE_LADDER if s < base))


def _render_zoom(cfg, slim_path, zoom, fonts, street_font, label_zooms):
    """Read, place and render one whole zoom of the main layer."""
    label = zoom in label_zooms
    points, placements, _rows = _place_zoom(cfg, slim_path, zoom, fonts, label)
    out_dir = os.path.join(cfg.raster_tile_dir, str(zoom))
    print(f"Raster z{zoom}: rendering tiles (labels={label}) ...")
    return _render_tiles(points, placements, fonts, street_font, label, {}, out_dir)


def _read_points(slim_path, zoom):
    """Return (points, complexes, rows) in zoom-level pixels.

    ``points`` is [(gx, gy, label, street), ...] with the label every point
    would prefer: the housenumber with the unit in front ("3-2441"), on its own
    street -- exactly what a point got before complexes existed.

    ``complexes`` is a parallel list holding (key, unit) for the doors of a
    complex (MIN_COMPLEX_DOORS or more sharing one street number) and None for
    everything else.  It is only a candidacy: whether a complex is actually
    drawn the short way is decided during placement, by whether the full labels
    fit.  See _place_complexes.

    ``rows`` is the third parallel list, holding each address as the source
    gave it -- (lon, lat, number, unit, street).  Rendering does not need it;
    the hidden-address report does, since a finding is only actionable if it
    names an address someone can look up.  See audit.py.
    """
    rows = []
    doors = defaultdict(int)
    with open(slim_path, encoding="utf-8") as f:
        for line in f:
            feat = json.loads(line)
            lon, lat = feat["geometry"]["coordinates"]
            props = feat["properties"]
            number = (props.get("housenumber") or "").strip()
            unit = (props.get("unit") or "").strip()
            street = props.get("street", "")
            rows.append((lon, lat, number, unit, street))
            if unit and street and number:
                doors[complex_key(street, number)] += 1

    points, complexes = [], []
    for lon, lat, number, unit, street in rows:
        gx, gy = lonlat_to_pixel(lon, lat, zoom)
        points.append((gx, gy, display_label(number, unit), street))
        key = complex_key(street, number) if unit and street and number else None
        complexes.append((key, unit)
                         if key is not None and doors[key] >= MIN_COMPLEX_DOORS
                         else None)
    return points, complexes, rows


def _shorten(points, complexes, crowded):
    """Points with the ``crowded`` complexes switched to bare unit + own key."""
    return [(gx, gy, c[1], c[0]) if c and c[0] in crowded else (gx, gy, text, st)
            for (gx, gy, text, st), c in zip(points, complexes)]


def _place_complexes(points, complexes, fonts):
    """Place labels, shortening only the complexes whose full labels don't fit.

    A complex is left exactly as it was before complexes existed -- full
    "3-2441" labels in the parent street's colour, no anchor label -- unless
    placing it that way loses a door.  Only then is it worth spending the
    ambiguity of a bare "3" and the space of an anchor label on it.

    Returns (points, placements, stats, crowded); ``points`` is the list the
    placements actually refer to, which is what the caller must render.
    """
    crowded = set()
    for _ in range(MAX_COMPLEX_PASSES):
        current = _shorten(points, complexes, crowded) if crowded else points
        placements, stats = _place_labels(current, fonts)
        newly = {complexes[i][0] for i, placement in enumerate(placements)
                 if placement is None and current[i][2] and complexes[i]}
        if not newly - crowded:
            return current, placements, stats, crowded
        crowded |= newly
    return current, placements, stats, crowded


def _place_labels(points, fonts):
    """Greedy multi-slot placement against a spatial hash.

    ``fonts`` maps size -> font, largest first (see ``font_ladder``).  Every
    slot is tried at the largest size before any is tried at the next size
    down, so shrinking is a last resort rather than a way to pack tighter.

    Occupancy is kept as two shapes, because a label and its leader claim very
    different amounts of ground: a label reserves its ink box (plus the halo
    pad), while a leader reserves only the line itself.  A candidate is free
    when its box misses every box and every leader, and its own leader crosses
    neither.  See the module docstring for what the old bbox reservation cost.

    Returns (placements, stats).  Each placement is None (dropped) or a
    (anchor, dx, dy, leader, size) tuple where (dx, dy) is the offset of the
    text anchor point from the dot centre, ``leader`` says whether to draw a
    connecting line, and ``size`` is the font it was placed at.
    """
    sizes = sorted(fonts, reverse=True)
    width_cache = {}

    def text_w(t, size):
        key = (t, size)
        w = width_cache.get(key)
        if w is None:
            w = fonts[size].getlength(t)
            width_cache[key] = w
        return w

    boxes = defaultdict(list)     # cell -> [(owner, box)]
    leaders = defaultdict(list)   # cell -> [(owner, segment)]

    def cells(x0, y0, x1, y1):
        cx0, cx1 = int(x0 // _GRID_CELL), int(x1 // _GRID_CELL)
        cy0, cy1 = int(y0 // _GRID_CELL), int(y1 // _GRID_CELL)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                yield (cx, cy)

    def seg_cells(seg):
        (x0, y0), (x1, y1) = seg
        return cells(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def collides(box, seg, owner):
        keys = set(cells(*box))
        if seg is not None:
            keys |= set(seg_cells(seg))
        for key in keys:
            for oid, other in boxes.get(key, ()):
                if oid == owner:
                    continue    # a point's own dot never blocks its own label
                if _boxes_overlap(box, other):
                    return True
                if seg is not None and _segment_hits_box(seg, other):
                    return True
            for oid, other in leaders.get(key, ()):
                if oid == owner:
                    continue
                if _segment_hits_box(other, box):
                    return True
                if seg is not None and _segments_cross(seg, other):
                    return True
        return False

    def add(box, seg, owner):
        # The box belongs to nobody once placed -- it may not be re-entered even
        # by its own point -- while a leader keeps its owner, so the dot it
        # starts from does not read as a crossing.
        for key in cells(*box):
            boxes[key].append((_NO_OWNER, box))
        if seg is not None:
            for key in seg_cells(seg):
                leaders[key].append((owner, seg))

    # Seed every dot first -- including points with no label text, which still
    # get drawn -- so no label can be placed on top of one.
    half = DOT_RADIUS + STROKE_WIDTH
    for i, (gx, gy, _text, _st) in enumerate(points):
        box = (gx - half, gy - half, gx + half, gy + half)
        for key in cells(*box):
            boxes[key].append((i, box))

    placements = [None] * len(points)
    stats = {"inner": 0, "leadered": 0, "dropped": 0, "shrunk": 0}
    order = sorted(range(len(points)), key=lambda i: (points[i][1], points[i][0]))
    pad = STROKE_WIDTH
    for i in order:
        gx, gy, text, _st = points[i]
        if not text:
            continue
        for size in sizes:
            w = text_w(text, size)
            for anchor, dx, dy, leader in _candidate_placements():
                label_bb = _anchor_bbox(anchor, gx + dx, gy + dy, w, size)
                box = (label_bb[0] - pad, label_bb[1] - pad,
                       label_bb[2] + pad, label_bb[3] + pad)
                seg = _leader_endpoints(gx, gy, label_bb) if leader else None
                if not collides(box, seg, i):
                    add(box, seg, i)
                    placements[i] = (anchor, dx, dy, leader, size)
                    stats["leadered" if leader else "inner"] += 1
                    if size != sizes[0]:
                        stats["shrunk"] += 1
                    break
            if placements[i] is not None:
                break
        else:
            stats["dropped"] += 1
    return placements, stats


def _boxes_overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def _segment_hits_box(seg, box):
    """Segment vs axis-aligned box, by the slab method.

    Clips the segment's parameter range against each of the box's four edges;
    what survives is the part inside the box, so an empty range means a miss.
    """
    (x0, y0), (x1, y1) = seg
    bx0, by0, bx1, by1 = box
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - bx0), (dx, bx1 - x0), (-dy, y0 - by0), (dy, by1 - y0)):
        if p == 0:
            if q < 0:       # parallel to this pair of edges and outside them
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return t0 <= t1


def _segments_cross(s1, s2):
    """True when two segments properly cross (touching endpoints do not count).

    Leaders radiate from dots, so two of them sharing an endpoint is not a
    crossing -- only a genuine X is.
    """
    def side(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])

    a, b = s1
    c, d = s2
    return (((side(c, d, a) > 0) != (side(c, d, b) > 0))
            and ((side(a, b, c) > 0) != (side(a, b, d) > 0)))


def _candidate_placements():
    r = DOT_RADIUS + LABEL_GAP
    yield ("lm",  r, 0.0, False)
    yield ("rm", -r, 0.0, False)
    yield ("mb", 0.0, -r, False)
    yield ("mt", 0.0,  r, False)
    for radius in (LEADER_R1, LEADER_R2):
        for ux, uy in _RING_DIRS:
            yield ("mm", radius * ux, radius * uy, True)


def _anchor_bbox(anchor, ax, ay, w, h):
    if anchor == "lm":
        return (ax, ay - h / 2, ax + w, ay + h / 2)
    if anchor == "rm":
        return (ax - w, ay - h / 2, ax, ay + h / 2)
    if anchor == "mb":
        return (ax - w / 2, ay - h, ax + w / 2, ay)
    if anchor == "mt":
        return (ax - w / 2, ay, ax + w / 2, ay + h)
    return (ax - w / 2, ay - h / 2, ax + w / 2, ay + h / 2)  # "mm"


def _leader_endpoints(gx, gy, label_bb):
    cx = (label_bb[0] + label_bb[2]) / 2
    cy = (label_bb[1] + label_bb[3]) / 2
    dx, dy = cx - gx, cy - gy
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    start = (gx + DOT_RADIUS * ux, gy + DOT_RADIUS * uy)
    x0, y0, x1, y1 = label_bb
    ts = []
    if dx:
        for bx in (x0, x1):
            t = (bx - gx) / dx
            y = gy + t * dy
            if t > 0 and y0 - 0.5 <= y <= y1 + 0.5:
                ts.append(t)
    if dy:
        for by in (y0, y1):
            t = (by - gy) / dy
            x = gx + t * dx
            if t > 0 and x0 - 0.5 <= x <= x1 + 0.5:
                ts.append(t)
    end = (gx + min(ts) * dx, gy + min(ts) * dy) if ts else (cx, cy)
    return start, end


def _placement_footprint(placement, gx, gy, w, h):
    """The rectangle a placed label and its leader draw into.

    This is the *drawn extent*, used to decide which tiles a label spills into
    (see _bucket_point).  It is deliberately not the collision shape: for a
    diagonal leader this rectangle is mostly empty, and treating it as occupied
    is what used to strand labels beside visible free space.
    """
    anchor, dx, dy, leader = placement
    ax, ay = gx + dx, gy + dy
    label_bb = _anchor_bbox(anchor, ax, ay, w, h)
    x0, y0, x1, y1 = label_bb
    if leader:
        (lx0, ly0), (lx1, ly1) = _leader_endpoints(gx, gy, label_bb)
        x0 = min(x0, lx0, lx1)
        x1 = max(x1, lx0, lx1)
        y0 = min(y0, ly0, ly1)
        y1 = max(y1, ly0, ly1)
    pad = STROKE_WIDTH
    return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)


def _bucket_point(tiles, gx, gy, text, st, placement, fonts):
    half = DOT_RADIUS + STROKE_WIDTH
    x0, y0, x1, y1 = gx - half, gy - half, gx + half, gy + half
    if placement is not None and text:
        size = placement[4]
        bx0, by0, bx1, by1 = _placement_footprint(
            placement[:4], gx, gy, fonts[size].getlength(text), size
        )
        x0, y0 = min(x0, bx0), min(y0, by0)
        x1, y1 = max(x1, bx1), max(y1, by1)
    tx0, tx1 = int(x0 // TILE_SIZE), int(x1 // TILE_SIZE)
    ty0, ty1 = int(y0 // TILE_SIZE), int(y1 // TILE_SIZE)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            tiles[(tx, ty)].append(
                (gx - tx * TILE_SIZE, gy - ty * TILE_SIZE, text, st, placement)
            )


def _render_tile(entries, fonts, street_font, draw_street, markers=()):
    img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    color_map = _assign_tile_colors({st for _, _, _, st, _ in entries})
    occupied = []  # bboxes (x0, y0, x1, y1) that floating street labels avoid

    # Detail markers go down first, so an address label drawn later covers the
    # dashes rather than the other way round -- the box is a hint, the number is
    # the content.  Their footprint joins ``occupied`` so the floating street
    # name does not land on the outline.
    _draw_markers(draw, markers, occupied)

    # Leader lines first so dots cover their endpoints cleanly.
    for ox, oy, text, st, placement in entries:
        if placement is None or not text or not placement[3]:
            continue
        anchor, dx, dy, _leader, size = placement
        ax, ay = ox + dx, oy + dy
        label_bb = _anchor_bbox(anchor, ax, ay, fonts[size].getlength(text), size)
        (sx, sy), (ex, ey) = _leader_endpoints(ox, oy, label_bb)
        draw.line([(sx, sy), (ex, ey)],
                  fill=_color_of(color_map, st), width=LEADER_WIDTH)

    # Dots (magenta -- unchanged so the visual field stays calm).
    for ox, oy, _, _, _ in entries:
        draw.ellipse(
            [ox - DOT_RADIUS, oy - DOT_RADIUS, ox + DOT_RADIUS, oy + DOT_RADIUS],
            fill=DOT_COLOR,
        )
        occupied.append((ox - DOT_RADIUS, oy - DOT_RADIUS,
                         ox + DOT_RADIUS, oy + DOT_RADIUS))

    # House number labels -- tinted by street.
    for ox, oy, text, st, placement in entries:
        if placement is None or not text:
            continue
        anchor, dx, dy, _leader, size = placement
        ax, ay = ox + dx, oy + dy
        draw.text(
            (ax, ay), text, font=fonts[size], fill=_color_of(color_map, st),
            stroke_width=STROKE_WIDTH, stroke_fill=HALO_COLOR, anchor=anchor,
        )
        bb = _anchor_bbox(anchor, ax, ay, fonts[size].getlength(text), size)
        occupied.append((bb[0] - STROKE_WIDTH, bb[1] - STROKE_WIDTH,
                         bb[2] + STROKE_WIDTH, bb[3] + STROKE_WIDTH))

    if draw_street:
        _draw_street_labels(draw, entries, street_font, occupied, color_map)

    return img


def _draw_markers(draw, markers, occupied):
    """Dash the detail-layer outline: white halo under, colour over.

    The halo is what keeps the box visible over dark aerial imagery, which is
    the backdrop this layer is normally read against.
    """
    for width, color in ((MARKER_HALO_WIDTH, MARKER_HALO), (MARKER_WIDTH, MARKER_COLOR)):
        for segment in markers:
            for x0, y0, x1, y1 in deep.dashes(segment):
                draw.line([(x0, y0), (x1, y1)], fill=color, width=width)
    for x0, y0, x1, y1 in markers:
        pad = MARKER_HALO_WIDTH
        occupied.append((min(x0, x1) - pad, min(y0, y1) - pad,
                         max(x0, x1) + pad, max(y0, y1) + pad))


def _draw_street_labels(draw, entries, street_font, occupied, color_map):
    """Place one floating street-name label per street per tile, in free space.

    Streets with fewer than STREET_LABEL_MIN_DOTS dots in this tile are
    skipped (likely just bucket-spillover from a neighbour).  If no free
    spot fits, the street is skipped here; the same name will be tried
    again in adjacent tiles.

    A complex is a street here, so this is also what writes "2441 GREENWICH DR"
    next to a courtyard of bare unit numbers, in the colour they are drawn in.
    """
    # Group by street, but only count dots whose centre is inside the tile so
    # we don't promote spillover entries.
    by_street = defaultdict(list)
    for ox, oy, _text, st, _pl in entries:
        if not st:
            continue
        if 0 <= ox < TILE_SIZE and 0 <= oy < TILE_SIZE:
            by_street[st].append((ox, oy))

    # Largest streets first -- they're the ones the user most wants oriented.
    for st, dots in sorted(by_street.items(), key=lambda kv: -len(kv[1])):
        if len(dots) < STREET_LABEL_MIN_DOTS:
            continue
        name = street_display(st)
        w = street_font.getlength(name)
        h = STREET_FONT_SIZE
        cx = sum(d[0] for d in dots) / len(dots)
        cy = sum(d[1] for d in dots) / len(dots)
        for ax, ay in _street_candidates(cx, cy, w, h):
            bb = (ax - w / 2 - STROKE_WIDTH, ay - h / 2 - STROKE_WIDTH,
                  ax + w / 2 + STROKE_WIDTH, ay + h / 2 + STROKE_WIDTH)
            if _bbox_collides(bb, occupied):
                continue
            draw.text(
                (ax, ay), name, font=street_font, fill=_color_of(color_map, st),
                stroke_width=STROKE_WIDTH, stroke_fill=HALO_COLOR, anchor="mm",
            )
            occupied.append(bb)
            break
        # If nothing fits: silently skip.  The dot colour still identifies it.


def _street_candidates(cx, cy, w, h):
    """Yield (ax, ay) anchor points spiralling out from the dots' centroid.

    Anchor is the label centre (anchor="mm").  Points are clamped so the label
    bbox stays inside the tile with STREET_TILE_MARGIN.
    """
    min_x = w / 2 + STROKE_WIDTH + STREET_TILE_MARGIN
    max_x = TILE_SIZE - w / 2 - STROKE_WIDTH - STREET_TILE_MARGIN
    min_y = h / 2 + STROKE_WIDTH + STREET_TILE_MARGIN
    max_y = TILE_SIZE - h / 2 - STROKE_WIDTH - STREET_TILE_MARGIN
    if min_x > max_x or min_y > max_y:
        return  # label too wide to ever fit in a tile
    seen = set()

    def clamp(x, y):
        return (max(min_x, min(max_x, x)), max(min_y, min(max_y, y)))

    def emit(x, y):
        key = (round(x), round(y))
        if key in seen:
            return None
        seen.add(key)
        return (x, y)

    # Centroid first.
    p = emit(*clamp(cx, cy))
    if p:
        yield p
    # Spiral outwards in 8 directions.
    for radius in (24, 48, 72, 100):
        for ux, uy in _RING_DIRS:
            p = emit(*clamp(cx + radius * ux, cy + radius * uy))
            if p:
                yield p


def _bbox_collides(box, others):
    x0, y0, x1, y1 = box
    for ox0, oy0, ox1, oy1 in others:
        if x0 < ox1 and x1 > ox0 and y0 < oy1 and y1 > oy0:
            return True
    return False
