# TODO

## Deep zoom for the last few dense addresses (decided, done)

**What it was.** Oakville left 96 of 71,049 addresses unlabelled at z19, the max
raster zoom, concentrated in a few apartment blocks where doors are 4-6px apart.
Since editors upscale past the deepest zoom, a label dropped there is invisible
at *every* zoom of the raster layer. The measured options were all bad: a
complete z20 cost +104 MB for ~17 remaining, a complete z21 +200 MB for zero.

**What it actually was.** Most of those 96 were not a density limit at all. A
leadered label reserved the axis-aligned bounding box of {label + leader}, and a
diagonal leader's bbox is mostly empty -- for one case, 329px2 reserved against
154px2 of ink. Neighbouring labels were being turned away from ground that was
visibly free. Colliding the leader as the *segment* it is took z19 from 96
unlabelled to 57 and z20 from 5 to zero, and cut shrunken labels from 95 to 32.
The old sizing table was measuring a bug.

**Why the sparse version was dropped.** The first cut rendered only the 36 tiles
that hold the stragglers -- 300 KB against 104 MB -- and published them as a
separate opt-in overlay. It was rejected on the grounds that mappers should not
have to add a second layer to see addresses: zooming in should just show them.
Folding a *sparse* zoom into the main layer is not an option, and this is
settled rather than assumed. Leaflet 1.9.4 `_tileReady` marks a failed tile
`loaded` and `active`, then calls `_pruneTiles`, whose `_retainParent` only runs
for tiles that are `current && !active` -- so a 404 child does not keep the
parent it was scaling up, and the area goes blank. A partly-filled zoom would
blank the map everywhere it had no tile.

**What shipped.**

- Exact leader geometry in `_place_labels` (box-vs-box, box-vs-segment,
  segment-vs-segment). Same invariants, more room.
- `[layer].raster_complete_to`: keep rendering deeper zooms, whole, until the
  audit finds nothing unlabelled. Stops as soon as it is clean, and drops a
  trailing zoom that rescues nobody (two doors on one coordinate).
- The published zoom range is read from the tiles on disk, so the landing page,
  the JOSM snippet and the ELI entry advertise what exists.
- A dashed marker box where a zoom cannot show everything, drawn as the outline
  of the union of adjacent tiles.

**Oakville today:** z16-20, 73,702 tiles, 273 MB (z20 alone is 45,500 tiles and
104 MB). Zero unlabelled addresses. 30 addresses are still *stacked* -- two
doors on one coordinate -- which no zoom separates and which `audit.py` reports
as the source problem it is.

**Still open:**

- Not verified in JOSM or iD on the real site. Nothing here should surprise
  them -- it is an ordinary zoom range now -- but nobody has walked it.
- Toronto cannot afford this: it is already ~1 GB, ~94% raster, and a z20 would
  roughly double it against Pages' ~1 GB limit. It stays at `raster_complete_to
  = 0` and keeps its residual. If that becomes unacceptable, the sparse tiles +
  a client that falls back to the parent is the design to revisit -- the
  machinery for rendering only selected tiles is still in `_render_tiles`.
- A denser candidate set (16 compass directions instead of 8) measured a further
  57 -> 11 at z19 on top of exact leaders, at the cost of doubling leader lines
  (316 -> 624). Not adopted, but for a city that cannot afford a completion zoom
  it is the cheapest remaining lever.
- Placement is greedy in north-to-south order, so a slot goes to whoever asks
  first rather than to whoever has fewest options. Placing most-constrained
  points first might beat both of the above without adding a single slot.

## Smaller follow-ups

- The complex anchor label ("2441 GREENWICH DR") is placed once per tile in free
  space and silently skipped when nothing fits, so a mapper can be looking at a
  courtyard of bare unit numbers with the anchor one tile over. Consider a
  fallback to the bare number ("2441") when the full name does not fit. This
  only bites in a *crowded* complex, which is exactly where free space is
  scarcest -- so it is worth checking on the ground.
- A crowded complex takes its own street key, which removes its dots from the
  parent street's count; the parent may then lose its name label in tiles the
  complex dominates. Probably correct; confirm it reads well.
- Crowding is decided per zoom, so a complex can be drawn with full labels at
  z19 and unit numbers at z18. That is the intended behaviour (each zoom shows
  the most it can), but the detail layer makes it sharper: a mapper who follows
  a marker box from z19 into z20 crosses that boundary deliberately, and the
  same building can read "13" on one side of it and "13-3025" on the other.
- The build never deletes tiles, so a zoom that renders fewer tiles than a
  previous run leaves the extras on disk and publishes them. Harmless today
  (they are stale but valid), and it predates the detail layer.
