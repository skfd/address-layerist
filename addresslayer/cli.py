"""CLI for the address-layer engine. Run from a city repo that has a layer.toml.

    addresslayer fetch     # fetch latest address GeoJSON (arcgis or static)
    addresslayer slim      # stream it into a slim GeoJSONL + meta
    addresslayer vector    # build vector (MVT) tiles via WSL tippecanoe
    addresslayer raster    # build labelled raster (PNG) tiles
    addresslayer site      # render the landing page
    addresslayer publish   # force-push build/site to gh-pages
    addresslayer build     # fetch + slim + vector + raster + site
    addresslayer update    # build + publish (the daily entry point)
    addresslayer onboard   # how to add a new city (prints guidance)
"""

import argparse
import os
import re
import sys

from addresslayer import config as _config
from addresslayer.fetch import fetch as _fetch
from addresslayer.slim import slim as _slim
from addresslayer.vector import build_vector
from addresslayer.raster import build_raster
from addresslayer.site import build_site
from addresslayer.publish import publish as _publish


def _banner(text):
    print(f"\n=== {text} ===")


def _latest_geojson(cfg):
    if not os.path.isdir(cfg.data_dir):
        raise SystemExit("No data/ directory. Run 'fetch' first.")
    files = sorted(
        f for f in os.listdir(cfg.data_dir)
        if f.startswith(f"{cfg.slug}-") and f.endswith(".geojson")
        and re.search(r"\d{4}-\d{2}-\d{2}", f)
    )
    if not files:
        raise SystemExit("No address GeoJSON in data/. Run 'fetch' first.")
    return os.path.join(cfg.data_dir, files[-1])


def cmd_fetch(cfg, args):
    _banner("Fetch")
    path, feats = _fetch(cfg, force=args.force)
    print(f"  {len(feats):,} features -> {path}")


def cmd_slim(cfg, args):
    _banner("Slim")
    _slim(cfg, _latest_geojson(cfg))


def cmd_vector(cfg, args):
    _banner("Vector tiles")
    build_vector(cfg)


def cmd_raster(cfg, args):
    _banner("Raster tiles")
    counts = build_raster(cfg)
    for zoom, n in sorted(counts.items()):
        print(f"  z{zoom}: {n:,} tiles")
        if n == 0:
            raise RuntimeError(f"Raster zoom {zoom} produced no tiles.")


def cmd_site(cfg, args):
    _banner("Site")
    build_site(cfg)


def cmd_publish(cfg, args):
    _banner("Publish")
    _publish(cfg)


def cmd_build(cfg, args):
    cmd_fetch(cfg, args)
    cmd_slim(cfg, args)
    cmd_vector(cfg, args)
    cmd_raster(cfg, args)
    cmd_site(cfg, args)


def cmd_update(cfg, args):
    cmd_build(cfg, args)
    cmd_publish(cfg, args)


COMMANDS = {
    "fetch": (cmd_fetch, "Fetch the latest address GeoJSON (arcgis or static)"),
    "slim": (cmd_slim, "Stream it into a slim GeoJSONL + meta"),
    "vector": (cmd_vector, "Build vector (MVT) tiles via WSL tippecanoe"),
    "raster": (cmd_raster, "Build labelled raster (PNG) tiles"),
    "site": (cmd_site, "Render the landing page"),
    "publish": (cmd_publish, "Force-push build/site to the gh-pages branch"),
    "build": (cmd_build, "fetch + slim + vector + raster + site"),
    "update": (cmd_update, "build + publish (daily scheduled-task entry point)"),
}

ONBOARD_HELP = """\
Onboarding a new city is a Claude Code task, not a script. See the skill:

  skills/onboard-city/SKILL.md   (in the address-layer engine repo)

It walks through: find the data source (reuse
ontario-address-changes/datasets/<slug>.toml if it exists), map which source
fields are number/street/unit/full, probe the feature count, set license +
attribution + dataset page, write layer.toml, run a build, and capture iD/JOSM
screenshots.
"""


def main():
    parser = argparse.ArgumentParser(
        prog="addresslayer", description="Address tile-layer builder"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("onboard", help="How to onboard a new city (prints guidance)")
    for name, (_, help_text) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "-c", "--config", default="layer.toml",
            help="Path to the city config (default: layer.toml)",
        )
        if name in ("fetch", "build", "update"):
            p.add_argument(
                "--force", action="store_true",
                help="Re-fetch even if the remote is unchanged",
            )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if args.command == "onboard":
        print(ONBOARD_HELP)
        return
    if not hasattr(args, "force"):
        args.force = False

    cfg = _config.load(getattr(args, "config", "layer.toml"))
    COMMANDS[args.command][0](cfg, args)


if __name__ == "__main__":
    main()
