# address-layerist

A reusable engine that turns any municipal **address-point** dataset into map
tiles for OpenStreetMap editors:

- **Vector tiles** (MVT) -- interactive in iD; click a point for its address tags.
- **Raster tiles** (PNG) -- house numbers drawn as text; a readable JOSM backdrop.
- A **landing page** with copy-paste "add this layer" instructions, published to
  GitHub Pages.

It generalizes [`toronto-addresses-layer`](../toronto-addresses-layer) and shares
its data-acquisition design with
[`ontario-address-changes`](../ontario-address-changes). Each city is a thin repo
(e.g. `oakville-address-layer`) that depends on this engine and carries one
`layer.toml`. Per-city repos (not one monorepo) because a single published site
can approach GitHub Pages' ~1 GB limit (Toronto alone is ~1 GB; raster is ~94%).

## Locked vs fuzzy

- **Locked (this engine, deterministic):** registry/config load, slim (of an
  input GeoJSON), tile math, vector (tippecanoe via WSL), raster (Pillow
  labeller), site templating, publish (orphan gh-pages). The engine does **not**
  acquire data -- it slims whatever GeoJSON it is pointed at (see *Data input*).
- **Fuzzy (a Claude Code skill):** onboarding a new city -- find the source, map
  number/street/unit/full, set licence/attribution, write `layer.toml`. See
  [`skills/onboard-city/SKILL.md`](skills/onboard-city/SKILL.md).

The `layer.toml` is the contract between the two halves. The data-source keys are
byte-compatible with `ontario-address-changes/datasets/<slug>.toml`, so a config
can be lifted from that registry.

## How a city repo uses it

```
oakville-address-layer/
  layer.toml            # the one per-city config (see the skill)
  run.py                # from addresslayerist.cli import main; main()
  requirements.txt      # -e ../address-layerist
  assets/               # optional overrides + iD.png / JOSM.png
```

```
pip install -e ../address-layerist      # once
addressvault pull <slug> --wait      # acquire the data (separate tool; not the engine)
python run.py build                  # slim + vector + raster + site
python run.py update                 # build + publish (daily entry point)
```

Individual steps: `slim vector raster site publish`. Run
`addresslayerist onboard` for onboarding guidance.

### Getting into the editors' layer pickers

```
python run.py eli                    # build/eli/<Id>.geojson, ready to PR
```

`eli` renders the **raster** layer as an
[Editor Layer Index](https://github.com/osmlab/editor-layer-index) entry -- the
index iD and JOSM read to populate their imagery pickers, so a mapper picks the
city off a list instead of copy-pasting a URL template off the landing page. The
vector layer has no equivalent: the index only describes imagery (`type: tms`).

It is deliberately not part of `build`. A submission happens once, and a missing
`license_url` should not be able to break a nightly tile build -- so `eli` prints
a warning for every field the index wants and the config lacks, plus the
fork/copy/PR steps. Set `[layer].boundary` to a GeoJSON of the municipal outline
before submitting; the fallback extent is the data's bounding box, which for most
cities also claims part of the neighbours.

### Data input

The engine never downloads. `slim` reads, in order: `--input PATH`; else the
newest `<slug>-DATE.geojson` in `input_dir` (a `layer.toml` key); else the newest
such file in `$ADDRESSVAULT_DIR`. It treats that directory as a plain folder of
dated dumps -- it has no knowledge of `address-vault`. Whatever populates it (the
`addressvault pull <slug>` step above, a manual download, anything) is the
caller's concern, so the daily scheduled task is `addressvault pull <slug> --wait
&& python run.py update` (`--wait` coalesces onto an in-flight pull instead of
racing or erroring).

## Key locked-in rules

- **Slim/MVT schema is derived** from canonical `[fields]`
  (`number->housenumber`, `street->street`, `full->addr`, `unit->unit`,
  `name=`the label, for iD), so raster/vector need no per-city code. Extra
  source props ship via `[layer].mvt_extra`.
- **One label rule for both layers** (`label.py`): the suffix is folded into the
  number (`335A`), then a unit leads it (`3-2280`). Both dimensions distinguish
  addresses that otherwise draw identically -- a townhouse block renders as
  `2280` repeated without the unit, and 335 collides with 335A without the
  suffix. `suffix` is consumed into `housenumber` rather than emitted as its own
  key, because `mvt_extra` shares that key space (Toronto passes through a
  `suffix` tag whose value is already inside its number).
- **Slim sanity is source-relative:** fail if fewer than 95% of the input features
  survive (no per-city magic count bounds).
- **The deepest raster zoom is audited** (`audit.py`): after placing labels there,
  the build lists every address that is *stacked* (its dot sits under another
  address's dot) or *unlabelled* (placement found nowhere for its number) and
  writes `build/<slug>-hidden-z<zoom>.csv`. Only the deepest zoom is checked,
  because clients upscale past it rather than fetching a deeper tile -- so a
  finding there is hidden at every zoom, whereas the same address at z17 is just
  waiting to be zoomed into. Stacked is almost always a source problem (a tower's
  doors on one centroid) and unlabelled a density one; the build only reports
  them, it never fails on them.

## Requirements

- Python >= 3.11. Installs `Pillow`, `ijson` (see pyproject). The engine has no
  data-acquisition dependency.
- An input GeoJSON the engine can find (see *Data input*) -- e.g. `ADDRESSVAULT_DIR`
  set to a folder of `<slug>-DATE.geojson` dumps, or `--input PATH`.
- WSL2 + tippecanoe for the vector step -- see [wsl-setup.md](wsl-setup.md).

## Tests

```
python -m pytest          # tile math + label rules + slim property-map + ELI entry
                          # + label placement + the hidden-address check
```
