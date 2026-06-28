"""CLI for the address-layerist engine. Run from a city repo that has a layer.toml.

    addresslayerist slim      # stream the input GeoJSON into a slim GeoJSONL + meta
    addresslayerist vector    # build vector (MVT) tiles via WSL tippecanoe
    addresslayerist raster    # build labelled raster (PNG) tiles
    addresslayerist site      # render the landing page
    addresslayerist publish   # force-push build/site to gh-pages
    addresslayerist build     # slim + vector + raster + site
    addresslayerist update    # build + publish (the daily entry point)
    addresslayerist onboard   # how to add a new city (prints guidance)

The engine builds tiles from an input GeoJSON; it does not acquire data. By
default it slims the newest ``<slug>-DATE.geojson`` in ``input_dir`` (layer.toml)
or, failing that, ``$ADDRESSVAULT_DIR`` -- whatever produced those files (e.g.
``addressvault pull <slug>``) is the caller's concern. Override with ``--input``.
"""

import argparse

from addresslayerist import config as _config
from addresslayerist.slim import slim as _slim
from addresslayerist.vector import build_vector
from addresslayerist.raster import build_raster
from addresslayerist.site import build_site
from addresslayerist.publish import publish as _publish


def _banner(text):
    print(f"\n=== {text} ===")


def cmd_slim(cfg, args):
    _banner("Slim")
    src = cfg.input_path(getattr(args, "input", None))
    print(f"  input: {src}")
    _slim(cfg, src)


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
    cmd_slim(cfg, args)
    cmd_vector(cfg, args)
    cmd_raster(cfg, args)
    cmd_site(cfg, args)


def cmd_update(cfg, args):
    cmd_build(cfg, args)
    cmd_publish(cfg, args)


COMMANDS = {
    "slim": (cmd_slim, "Stream the input GeoJSON into a slim GeoJSONL + meta"),
    "vector": (cmd_vector, "Build vector (MVT) tiles via WSL tippecanoe"),
    "raster": (cmd_raster, "Build labelled raster (PNG) tiles"),
    "site": (cmd_site, "Render the landing page"),
    "publish": (cmd_publish, "Force-push build/site to the gh-pages branch"),
    "build": (cmd_build, "slim + vector + raster + site"),
    "update": (cmd_update, "build + publish (daily scheduled-task entry point)"),
}

ONBOARD_HELP = """\
Onboarding a new city is a Claude Code task, not a script. See the skill:

  skills/onboard-city/SKILL.md   (in the address-layerist engine repo)

It walks through: find the data source (reuse
ontario-address-changes/datasets/<slug>.toml if it exists), map which source
fields are number/street/unit/full, probe the feature count, set license +
attribution + dataset page, write layer.toml, run a build, and capture iD/JOSM
screenshots.
"""


def main():
    parser = argparse.ArgumentParser(
        prog="addresslayerist", description="Address tile-layer builder"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("onboard", help="How to onboard a new city (prints guidance)")
    for name, (_, help_text) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "-c", "--config", default="layer.toml",
            help="Path to the city config (default: layer.toml)",
        )
        if name in ("slim", "build", "update"):
            p.add_argument(
                "--input",
                help="Input GeoJSON path (default: newest <slug>-DATE.geojson in input_dir)",
            )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if args.command == "onboard":
        print(ONBOARD_HELP)
        return

    cfg = _config.load(getattr(args, "config", "layer.toml"))
    COMMANDS[args.command][0](cfg, args)


if __name__ == "__main__":
    main()
