# TODO

## Deep zoom for the last few dense addresses (undecided)

**Where it stands.** After the #2 collision fix, the per-zoom font size, the
shrink ladder and complex-as-street, Oakville still leaves **96 of 71,049**
addresses unlabelled at z19 (the max raster zoom). They are concentrated in a
handful of apartment blocks -- POST RD, HAYS BLVD, GREENWICH DR, SIXTH LINE --
where doors are ~4-6px apart at z19.

z19 is the last rendered zoom and the ELI entry advertises `max_zoom: 19`, so
editors upscale past it. A label dropped at z19 is therefore invisible at
*every* zoom in the raster layer. The vector layer is unaffected: it carries all
71,049 features with their `name` at z19 (verified by decoding the mbtiles) and
overzooms client-side, so nothing is permanently hidden there.

**Measured options** (Oakville, before complex-as-street; re-measure before
acting -- the residual is smaller now):

| max raster zoom | unlabelled | tiles added | size added |
| --------------- | ---------- | ----------- | ---------- |
| z19 (today)     | 96         | --          | --         |
| z20             | ~17-20     | +45,501     | +104 MB    |
| z21             | 0          | +114,930    | +200 MB    |

Shrinking below 7px and adding wider leader rings were both tried and rejected:
they move z20 from 20 to 17 and z19 from 154 to 149. Zero requires z21.

**The sparse idea, and why it is not one layer.** Only **58** of 41,166 z20
tiles contain a point unresolved at z19 (0.14%); at z21, **17** of 60,205
(0.03%). So the useful deep tiles number ~75, not ~115,000. But XYZ clients do
not fall back to a parent tile when a child 404s, and the zoom range is
advertised once (ELI `max_zoom`, JOSM `tms[min,max]`, Leaflet `maxNativeZoom`).
Ship a sparse pyramid under the same URL and every *other* area goes blank at
z20+ -- addresses vanish exactly when a mapper zooms in. Strictly worse than
today.

**The form that does work: a second, opt-in detail layer.** Publish the ~75 deep
tiles under their own URL, documented on the site next to the existing vector
and raster URLs (not a second ELI entry). It composes unusually well because in
exactly those spots the main layer has *no* labels -- they are the dropped ones
-- so the overlay adds the missing numbers rather than duplicating anything.
Only the dots double up (blurry upscaled + crisp); having the detail tiles draw
labels and leaders only would avoid that.

**Open decision:**

- complete z20 (+104 MB, ~17 left, works for everyone, no discovery step), or
- complete z20 + z21 (+200 MB, zero left, automatic), or
- complete z20 + sparse z21 detail layer (+104 MB, zero left, opt-in), or
- leave z19 as the max and treat the vector layer as the complete view.

**Constraint that matters for other cities:** a published site can approach
GitHub Pages' ~1 GB limit already -- Toronto is ~1 GB, ~94% of it raster (see
README). Whatever is chosen should be a per-city `layer.toml` setting, not an
engine-wide default, or Toronto cannot adopt it.

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
  the most it can), but it means the same building reads differently as you
  zoom. Worth a look before assuming it is fine.
