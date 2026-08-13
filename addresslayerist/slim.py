"""Stream a city's address GeoJSON into a slim GeoJSONL + a meta sidecar.

The source can be large (Toronto ~590 MB), so it is parsed as a stream with
ijson and never loaded whole. The slim output (one compact Feature per line) is
the shared input to both tile builders.

Output property keys are DERIVED from the canonical field map, so every city
yields the same schema and the raster/vector code needs no per-city changes:

    canonical number -> housenumber
    canonical street -> street
    canonical full   -> addr
    canonical unit   -> unit
    canonical suffix -> folded into housenumber ("335" + "A" -> "335A")
    [layer].mvt_extra -> extra source-prop -> short-key pairs (e.g. Toronto class)

The suffix is folded in here rather than carried as its own short key on
purpose: `mvt_extra` shares this key space, and Toronto already publishes a
passthrough tag named `suffix` whose value is *already* inside its number. A
downstream consumer reading a `suffix` key could not tell the two apart, so the
one place that knows the difference -- the canonical field map, right here --
resolves it once.

`name` is the label text, not a bare housenumber: a unit, when present, leads it
("3-2280") so a townhouse block doesn't read as one number repeated. See label.py.

The meta sidecar records the feature count and lon/lat bbox (the site uses the
bbox midpoint to centre its preview map).
"""

import json
import os

import ijson

from .label import display_label, housenumber

# canonical field name -> slim/MVT short key
_CANON_TO_SHORT = {
    "number": "housenumber",
    "street": "street",
    "full": "addr",
    "unit": "unit",
}

# Canonical fields that are consumed into another key instead of being emitted
# under one of their own. Kept out of _CANON_TO_SHORT so the slim schema stays
# exactly the set of keys above.
_CANON_SUFFIX = "suffix"

# Slim should keep ~every feature; only geometry-less / out-of-range points drop.
# Replaces per-city absolute count bounds with a source-relative sanity check.
MIN_KEEP_RATIO = 0.95


def _property_map(cfg):
    """source property key -> slim short key, from canonical fields + extras."""
    out = {}
    for canon, src in cfg.fields.items():
        short = _CANON_TO_SHORT.get(canon)
        if short and src:
            out[src] = short
    out.update(cfg.mvt_extra or {})
    return out


def _text(val):
    """Source value as clean text, or "" for the ways a source says 'absent'."""
    if val is None:
        return ""
    text = str(val).strip()
    return "" if text == "None" else text


def slim(cfg, src_path):
    """Stream ``src_path`` into ``cfg.slim_path`` (+ meta). Returns the slim path."""
    print(f"Slimming {src_path} ...")
    os.makedirs(cfg.data_dir, exist_ok=True)

    prop_map = _property_map(cfg)
    if "housenumber" not in prop_map.values():
        raise RuntimeError(
            "No 'number' field is mapped, but the raster labeller needs "
            "housenumber. Set [fields].number in layer.toml."
        )
    suffix_src = cfg.fields.get(_CANON_SUFFIX)

    count = skipped = 0
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    with open(src_path, "rb") as src, \
            open(cfg.slim_path, "w", encoding="utf-8") as out:
        for feature in ijson.items(src, "features.item"):
            point = _first_point(feature.get("geometry") or {})
            if point is None:
                skipped += 1
                continue
            lon, lat = point
            props_in = feature.get("properties") or {}
            props_out = {}
            for src_key, out_key in prop_map.items():
                text = _text(props_in.get(src_key))
                if text:
                    props_out[out_key] = text
            if suffix_src and "housenumber" in props_out:
                props_out["housenumber"] = housenumber(
                    props_out["housenumber"], _text(props_in.get(suffix_src))
                )
            # iD's Custom Map Data draws labels from a feature's `name` property,
            # so mirror the display label there to match the raster layer.
            label = display_label(props_out.get("housenumber"),
                                  props_out.get("unit"))
            if label:
                props_out["name"] = label
            out.write(json.dumps({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props_out,
            }) + "\n")
            count += 1
            min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
            if count % 100_000 == 0:
                print(f"  {count:,} features ...")

    meta = {"count": count}
    if count:
        meta.update(min_lon=min_lon, min_lat=min_lat,
                    max_lon=max_lon, max_lat=max_lat)
    with open(cfg.meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

    raw = count + skipped
    print(f"Done: {cfg.slim_path} ({count:,} features, {skipped:,} skipped)")
    if raw == 0:
        raise RuntimeError("Source contained no features -- aborting.")
    if count / raw < MIN_KEEP_RATIO:
        raise RuntimeError(
            f"Slim kept only {count:,}/{raw:,} features ({count / raw:.0%}); "
            f"expected >= {MIN_KEEP_RATIO:.0%}. Source geometry looks wrong -- "
            f"aborting."
        )
    if cfg.expected_min and count < cfg.expected_min:
        raise RuntimeError(
            f"Slim count {count:,} is below expected_min {cfg.expected_min:,} "
            f"-- aborting."
        )
    return cfg.slim_path


def _first_point(geom):
    """Extract a single (lon, lat) tuple from a Point or MultiPoint geometry."""
    coords = geom.get("coordinates")
    if not coords:
        return None
    gtype = geom.get("type")
    if gtype == "Point":
        pt = coords
    elif gtype == "MultiPoint":
        pt = coords[0]
    else:
        return None
    try:
        lon, lat = float(pt[0]), float(pt[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return lon, lat
