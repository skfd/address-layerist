"""Emit a ready-to-PR Editor Layer Index (ELI) entry for the raster layer.

The [Editor Layer Index](https://github.com/osmlab/editor-layer-index) is what
puts a layer in iD's and JOSM's imagery pickers, so a mapper picks the city off a
list instead of copy-pasting a URL template from the landing page. An entry is a
single GeoJSON Feature: the properties describe the tile source, the geometry is
the region it covers -- and the engine already knows every field.

Only the **raster** layer is submittable. ELI indexes imagery (``type: tms``) and
has no notion of an MVT source, so the vector layer stays a copy-paste affair.

The output is written to ``build/eli/<id>.geojson`` -- the exact file to drop into
the index's ``sources/`` tree and open a PR with. It is deliberately not part of
``build``: a submission is a one-off, and a missing licence URL should not break
a nightly tile build.

Fields the index wants but the engine cannot know (country code, privacy policy,
a boundary tighter than the data's bbox) come from ``[layer]`` keys. Each one
left unset is reported as a warning rather than an error, so a first run shows
the whole gap list at once instead of one key per failed run.
"""

import json
import os
import re
from urllib.parse import urlparse

# Every layer this engine builds is hosted on GitHub Pages, so its privacy
# policy is GitHub's. Only used when the pages URL actually points there.
_GITHUB_PAGES_HOST = ".github.io"
_GITHUB_PRIVACY_URL = (
    "https://docs.github.com/en/site-policy/privacy-policies/"
    "github-general-privacy-statement"
)

# ELI's schema: id is ^[-_.A-Za-z0-9]+$, country_code is ^[A-Z]{2}$. Slug word
# boundaries are anything that isn't alphanumeric, so "greater-sudbury" becomes
# two capitalised words rather than one.
_SLUG_WORDS = re.compile(r"[^A-Za-z0-9]+")
_COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")

_ID_SUFFIX = "Addresses"
_CATEGORIES = ("photo", "map", "historicmap", "osmbasedmap", "historicphoto",
               "qa", "elevation", "other")


def build_eli(cfg):
    """Write the ELI entry for ``cfg``'s raster layer. Returns the file path."""
    if not cfg.pages_url:
        raise RuntimeError(
            "No [layer].pages_url in the config -- without it there is no tile "
            "URL to submit."
        )
    if cfg.eli_category not in _CATEGORIES:
        raise RuntimeError(
            f"[layer].eli_category must be one of {_CATEGORIES}, "
            f"not {cfg.eli_category!r}."
        )
    if cfg.country_code and not _COUNTRY_CODE.match(cfg.country_code):
        raise RuntimeError(
            f"[layer].country_code must be an upper-case ISO 3166-1 alpha-2 "
            f"code (e.g. 'CA'), not {cfg.country_code!r}."
        )

    warnings = []
    geometry = _geometry(cfg, warnings)
    entry_id = _entry_id(cfg)
    properties = {
        "id": entry_id,
        "name": cfg.title_or_default,
        "type": "tms",
        "category": cfg.eli_category,
        "url": f"{cfg.pages_url}/tiles/raster/{{zoom}}/{{x}}/{{y}}.png",
        # The zooms actually published, which includes any completion zoom the
        # build added: advertising less would leave the deepest tiles unfetched,
        # advertising more would send editors to tiles that do not exist.
        "min_zoom": min(cfg.built_raster_zooms),
        "max_zoom": max(cfg.built_raster_zooms),
        # Labels on transparent tiles: an overlay, never a background layer.
        "overlay": True,
        "description": cfg.description or _description(cfg),
    }
    if cfg.country_code:
        properties["country_code"] = cfg.country_code
    else:
        warnings.append(
            "no [layer].country_code -- set the ISO 3166-1 alpha-2 code "
            "(e.g. 'CA') so the index can file the entry by country."
        )
    attribution = _attribution(cfg, warnings)
    if attribution:
        properties["attribution"] = attribution
    if cfg.license_url:
        properties["license_url"] = cfg.license_url
    else:
        warnings.append(
            "no [layer].license_url -- the index wants a link to the terms the "
            "data is published under."
        )
    privacy = _privacy_policy_url(cfg, warnings)
    if privacy is not None:
        properties["privacy_policy_url"] = privacy

    feature = {"type": "Feature", "properties": properties, "geometry": geometry}

    os.makedirs(cfg.eli_dir, exist_ok=True)
    path = os.path.join(cfg.eli_dir, f"{entry_id}.geojson")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feature, f, indent=4, ensure_ascii=False)
        f.write("\n")

    print(f"ELI entry written: {path}")
    for warning in warnings:
        print(f"  ! {warning}")
    _print_next_steps(cfg, entry_id)
    return path


def _entry_id(cfg):
    """The index-wide unique id, e.g. ``Toronto-Addresses``.

    It ends up in the ``imagery_used`` changeset tag of every edit made with the
    layer open, so it wants to read as a name, not as a slug.
    """
    if cfg.eli_id:
        return cfg.eli_id
    words = [w for w in _SLUG_WORDS.split(cfg.slug) if w]
    return "-".join([w[:1].upper() + w[1:] for w in words] + [_ID_SUFFIX])


def _description(cfg):
    """A short English sentence for the imagery picker's info panel."""
    return (
        f"Address points published by {cfg.provider}, drawn as house-number "
        f"labels. A reference overlay for surveying addresses -- check each "
        f"one on the ground; not a bulk-import source."
    )


def _attribution(cfg, warnings):
    if not cfg.attribution:
        warnings.append(
            "no [layer].attribution -- open-data licences almost always "
            "require an attribution line."
        )
        return None
    # The dataset page is the more useful landing spot for a mapper following
    # the credit in the editor; the licence page is the fallback.
    url = cfg.dataset_page or cfg.license_url
    attribution = {"text": cfg.attribution, "required": True}
    if url:
        attribution["url"] = url
    return attribution


def _privacy_policy_url(cfg, warnings):
    """The host's privacy policy: configured, or GitHub's for a Pages host."""
    if cfg.privacy_policy_url:
        return cfg.privacy_policy_url
    host = urlparse(cfg.pages_url).hostname or ""
    if host.endswith(_GITHUB_PAGES_HOST):
        return _GITHUB_PRIVACY_URL
    warnings.append(
        "no [layer].privacy_policy_url -- the index requires one for every "
        "source (or the literal `false` when the host has none)."
    )
    return None


def _geometry(cfg, warnings):
    """The covered region: the configured boundary, else the data's bbox.

    A bbox is accepted by the index but is a blunt claim -- for a city it
    typically covers a good deal of neighbouring territory, and the picker uses
    it to decide who is offered the layer.
    """
    if cfg.boundary:
        path = cfg.boundary if os.path.isabs(cfg.boundary) else os.path.join(
            cfg.project_dir, cfg.boundary)
        if not os.path.isfile(path):
            raise RuntimeError(f"[layer].boundary not found: {path}")
        return _boundary_geometry(path)

    meta = _load_meta(cfg)
    if "min_lat" not in meta:
        raise RuntimeError(
            f"No bbox in {cfg.meta_path}. Run 'slim' first, or set "
            f"[layer].boundary to a GeoJSON of the city outline."
        )
    warnings.append(
        "geometry is the data's bounding box -- set [layer].boundary to the "
        "city outline (a GeoJSON polygon) so the layer is only offered inside "
        "the area it actually covers."
    )
    west, south = round(meta["min_lon"], 6), round(meta["min_lat"], 6)
    east, north = round(meta["max_lon"], 6), round(meta["max_lat"], 6)
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north],
                         [west, north], [west, south]]],
    }


def _boundary_geometry(path):
    """Read a Polygon/MultiPolygon out of a geometry, Feature or collection."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    polygons = []
    for geom in _geometries(data):
        if geom.get("type") == "Polygon":
            polygons.append(geom["coordinates"])
        elif geom.get("type") == "MultiPolygon":
            polygons.extend(geom["coordinates"])
    if not polygons:
        raise RuntimeError(
            f"{path}: no Polygon or MultiPolygon found. The index needs an "
            f"area geometry for the layer's extent."
        )
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _geometries(node):
    """Yield the geometry objects of a geometry, Feature or FeatureCollection."""
    kind = node.get("type")
    if kind == "FeatureCollection":
        for feature in node.get("features") or []:
            yield from _geometries(feature)
    elif kind == "Feature":
        geom = node.get("geometry")
        if geom:
            yield from _geometries(geom)
    elif kind == "GeometryCollection":
        for geom in node.get("geometries") or []:
            yield from _geometries(geom)
    elif kind:
        yield node


def _load_meta(cfg):
    if not os.path.isfile(cfg.meta_path):
        return {}
    with open(cfg.meta_path, encoding="utf-8") as f:
        return json.load(f)


def _print_next_steps(cfg, entry_id):
    country = (cfg.country_code or "??").lower()
    print(
        "\nTo submit:\n"
        "  1. Fork https://github.com/osmlab/editor-layer-index\n"
        f"  2. Copy the file to sources/<continent>/{country}/<subdivision>/"
        f"{entry_id}.geojson\n"
        "     (e.g. sources/north-america/ca/on/ -- follow the neighbours)\n"
        "  3. Open a PR; the index's CI validates the entry against its schema."
    )
