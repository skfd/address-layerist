"""Load a per-city ``layer.toml`` into a Config object.

The working directory is the city repo (data/, build/ and any asset overrides
live there). Engine default assets live inside this package; a city overrides
one by dropping a file of the same name in its own ``assets/``.

The data-source keys (slug/provider/data_url/access/format/license_name/
source_crs/[fields]) are intentionally byte-compatible with
``ontario-address-changes/datasets/<slug>.toml`` so a config can be lifted from
that registry. Layer-only settings live under a ``[layer]`` table.
"""

import os
import tomllib
from dataclasses import dataclass, field

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE_ASSETS_DIR = os.path.join(PACKAGE_DIR, "assets")

# The engine builds tiles from an input GeoJSON; it does not fetch. Only the
# tiling-relevant keys are required. The data-source keys (data_url/access/format)
# remain optional for byte-compatibility with ontario-address-changes datasets.
_REQUIRED = ("slug", "provider")
_VALID_ACCESS = ("arcgis", "static")
_VALID_FORMAT = ("geojson", "shapefile")


@dataclass
class Config:
    # --- data source (shared shape with ontario-address-changes) ---
    slug: str
    provider: str
    data_url: str = ""                   # data-source keys: kept for registry
    access: str = ""                     # byte-compatibility; the engine does not fetch
    format: str = ""
    license_name: str = ""
    source_crs: str = ""                 # e.g. "EPSG:2952"; reproject to WGS84
    fields: dict = field(default_factory=dict)   # canonical -> source property
    input_dir: str = ""                  # where to find <slug>-DATE.geojson (default: $ADDRESSVAULT_DIR)

    # --- layer-only ([layer] table) ---
    title: str = ""
    github_repo: str = ""
    pages_url: str = ""
    dataset_page: str = ""
    license_url: str = ""
    attribution: str = ""
    changes_url: str = ""                 # optional sibling change-tracker site
    head_extra: str = ""                  # raw HTML injected before </head> (e.g. analytics)
    layer_name: str = "addresses"
    wsl_distro: str = "Ubuntu"
    vector_minzoom: int = 12
    vector_maxzoom: int = 19
    raster_zooms: list = field(default_factory=lambda: [16, 17, 18, 19])
    raster_label_zooms: list = field(default_factory=lambda: [17, 18, 19])
    mvt_extra: dict = field(default_factory=dict)  # extra source prop -> short key
    expected_min: int = 0                # optional absolute floor for slim count

    project_dir: str = field(default_factory=os.getcwd)

    # --- derived paths (all under the city repo) ---
    @property
    def data_dir(self):
        return os.path.join(self.project_dir, "data")

    @property
    def build_dir(self):
        return os.path.join(self.project_dir, "build")

    @property
    def site_dir(self):
        return os.path.join(self.build_dir, "site")

    @property
    def logs_dir(self):
        return os.path.join(self.project_dir, "logs")

    @property
    def override_assets_dir(self):
        return os.path.join(self.project_dir, "assets")

    @property
    def mbtiles_path(self):
        return os.path.join(self.build_dir, f"{self.slug}.mbtiles")

    @property
    def slim_path(self):
        return os.path.join(self.data_dir, f"{self.slug}-slim.geojsonl")

    @property
    def meta_path(self):
        return os.path.join(self.data_dir, f"{self.slug}-meta.json")

    def input_path(self, override=None):
        """The GeoJSON to slim. ``override`` (--input) wins; otherwise the newest
        ``<slug>-DATE.geojson`` in ``input_dir`` (default: $ADDRESSVAULT_DIR).

        The engine treats that directory as a plain folder of dated dumps -- it
        has no knowledge of how the files got there (e.g. ``addressvault pull``)."""
        if override:
            return override
        import glob
        d = self.input_dir or os.environ.get("ADDRESSVAULT_DIR")
        if not d:
            raise SystemExit(
                "No input dir. Set ADDRESSVAULT_DIR, set input_dir in layer.toml, "
                "or pass --input PATH."
            )
        hits = sorted(glob.glob(os.path.join(d, f"{self.slug}-*.geojson")))
        if not hits:
            raise SystemExit(
                f"No {self.slug}-*.geojson in {d}. Provide the data first "
                f"(e.g. 'addressvault pull {self.slug}')."
            )
        return hits[-1]  # ISO dates in the name sort lexically -> newest last

    @property
    def vector_tile_dir(self):
        return os.path.join(self.site_dir, "tiles", "vector")

    @property
    def raster_tile_dir(self):
        return os.path.join(self.site_dir, "tiles", "raster")

    @property
    def title_or_default(self):
        return self.title or f"{self.provider} Address Points"

    def asset(self, name):
        """Path to an asset, preferring a city override in ./assets/."""
        override = os.path.join(self.override_assets_dir, name)
        if os.path.isfile(override):
            return override
        return os.path.join(ENGINE_ASSETS_DIR, name)


def load(path="layer.toml", project_dir=None):
    """Parse and validate a city config. Raises SystemExit with a clear message."""
    project_dir = os.path.abspath(project_dir or os.getcwd())
    cfg_path = path if os.path.isabs(path) else os.path.join(project_dir, path)
    if not os.path.isfile(cfg_path):
        raise SystemExit(
            f"No config file: {cfg_path}. Run from a city repo containing a "
            f"layer.toml (see 'addresslayerist onboard')."
        )
    with open(cfg_path, "rb") as f:
        raw = tomllib.load(f)

    missing = [k for k in _REQUIRED if not raw.get(k)]
    if missing:
        raise SystemExit(f"{cfg_path}: missing required keys: {missing}")
    if raw.get("access") and raw["access"] not in _VALID_ACCESS:
        raise SystemExit(f"{cfg_path}: access must be one of {_VALID_ACCESS}")
    if raw.get("format") and raw["format"] not in _VALID_FORMAT:
        raise SystemExit(f"{cfg_path}: format must be one of {_VALID_FORMAT}")

    layer = raw.get("layer", {})
    return Config(
        slug=raw["slug"],
        provider=raw["provider"],
        data_url=raw.get("data_url", ""),
        access=raw.get("access", ""),
        format=raw.get("format", ""),
        license_name=raw.get("license_name", ""),
        source_crs=raw.get("source_crs", ""),
        fields=raw.get("fields", {}),
        input_dir=raw.get("input_dir", ""),
        title=layer.get("title", ""),
        github_repo=layer.get("github_repo", ""),
        pages_url=layer.get("pages_url", ""),
        dataset_page=layer.get("dataset_page", ""),
        license_url=layer.get("license_url", ""),
        attribution=layer.get("attribution", ""),
        changes_url=layer.get("changes_url", ""),
        head_extra=layer.get("head_extra", ""),
        layer_name=layer.get("layer_name", "addresses"),
        wsl_distro=layer.get("wsl_distro", "Ubuntu"),
        vector_minzoom=layer.get("vector_minzoom", 12),
        vector_maxzoom=layer.get("vector_maxzoom", 19),
        raster_zooms=layer.get("raster_zooms", [16, 17, 18, 19]),
        raster_label_zooms=layer.get("raster_label_zooms", [17, 18, 19]),
        mvt_extra=layer.get("mvt_extra", {}),
        expected_min=layer.get("expected_min", 0),
        project_dir=project_dir,
    )
