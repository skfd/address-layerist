"""address-layerist: build OSM reference address tile layers from any municipal
address-point dataset. Locked, deterministic pipeline; per-city onboarding is a
Claude Code skill that writes a layer.toml (see skills/onboard-city).

No ``__version__`` here on purpose: pyproject is the one source, and the CLI
reads it back with importlib.metadata (see cli.py _engine_banner), so there is
nothing to keep in step.
"""
