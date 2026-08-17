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

**Why the sparse version was dropped, and then adopted (2026-08-13).** The first
cut rendered only the 36 tiles that hold the stragglers -- 300 KB against 104 MB
-- and published them as a *separate opt-in overlay*. That packaging was
rejected on the grounds that mappers should not have to add a second layer to
see addresses: zooming in should just show them. Folding a sparse zoom into the
main layer was then rejected too, on client behaviour: Leaflet 1.9.4
`_tileReady` marks a failed tile `loaded` and `active`, then calls
`_pruneTiles`, whose `_retainParent` only runs for tiles that are
`current && !active` -- so a 404 child does not keep the parent it was scaling
up, and the area goes blank.

Toronto reopened it. A whole z20 there is **283,239 tiles / 666 MB** on a site
already at 1,068 MB, against **180 tiles** sparse -- the difference between
publishable and not, where for Oakville it had only been 104 MB against 300 KB.
The Leaflet finding also turned out to be narrower than it reads: Leaflet is the
landing page's *preview*, not the audience, and the preview can simply stop at
the deepest whole zoom (`maxNativeZoom`) and upscale, which costs it the deep
labels and nothing else. The 404s were accepted as a deliberate trade, and a
completion zoom now renders `only=level.cells`. The marker box, previously a
hint, is now what makes the sparse zoom navigable.

**What shipped.**

- Exact leader geometry in `_place_labels` (box-vs-box, box-vs-segment,
  segment-vs-segment). Same invariants, more room.
- `[layer].raster_complete_to`: keep adding deeper zooms until the audit finds
  nothing unlabelled. Stops as soon as it is clean, and drops a trailing zoom
  that rescues nobody (two doors on one coordinate). Each such zoom ships only
  the tiles holding its stragglers; the rest of it is 404 on purpose.
- The published zoom range is read from the tiles on disk, so the landing page,
  the JOSM snippet and the ELI entry advertise what exists.
- A dashed marker box where a zoom cannot show everything, drawn as the outline
  of the union of adjacent tiles.
- The build deletes. Each zoom prunes the tiles it did not write this run, and
  a zoom directory the build no longer renders at all is dropped whole. See
  "The build never deleted tiles" below.

**Oakville today:** z16-20, 73,702 tiles, 273 MB (z20 alone is 45,500 tiles and
104 MB). Zero unlabelled addresses. 30 addresses are still *stacked* -- two
doors on one coordinate -- which no zoom separates and which `audit.py` reports
as the source problem it is.

**Still open:**

- **Not verified in JOSM or iD on the real site, and this now matters more than
  it did.** With a sparse completion zoom, an editor that mishandles a 404 the
  way Leaflet does would blank the deepest zoom over most of the city rather
  than upscale the parent. Whoever walks it should zoom past the deepest whole
  zoom *away* from a marker box, which is the case that 404s.
- Oakville's z20 was rendered whole (45,500 tiles, 104 MB) before the switch and
  those tiles are still on disk. Its next build now prunes them; that build will
  publish a ~104 MB deletion and should be watched, since it is the first time
  the engine removes tiles on a real city.
- A denser candidate set (16 compass directions instead of 8) measured a further
  57 -> 11 at z19 on top of exact leaders, at the cost of doubling leader lines
  (316 -> 624). Measured again on Toronto: 251 -> 186. Not adopted; a completion
  zoom is now cheap enough that this is the more expensive lever of the two.
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
## The build never deleted tiles (done, 2026-08-17)

Rendering writes into the published tree and `publish` snapshots whatever is on
disk, so a run that rendered *fewer* tiles than the last one left the surplus
there and published it again. It predated the detail layer and read as harmless
-- the extras are stale but valid -- until a completion zoom turned sparse and
Oakville's z20 went from 45,500 tiles to ~36 with the other 45,464 still going
out. An orphaned *zoom* is worse than orphaned tiles: `built_raster_zooms` reads
the published range off the disk, so the landing page, the JOSM snippet and the
ELI entry would go on advertising a zoom the build had stopped producing.

`_render_tiles` now prunes after the write instead of clearing before it: one
`scandir` per zoom against the key set it just rendered, deleting only actual
orphans (normally none) rather than re-encoding every tile that did not change,
and a crash mid-render leaves the previous zoom intact rather than half of one.
Emptied columns go with their tiles. `build_raster` then drops any zoom
directory not in the run's counts. Only `<x>/<y>.png` is touched. The contrast
with `vector.py`, which does clear first, is deliberate: tippecanoe explodes
into an empty directory anyway, and that clear is racing a WSL idle timeout
rather than saving work.
