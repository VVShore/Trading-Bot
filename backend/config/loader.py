"""
Configuration loader.

Loads and validates YAML config into an AppConfig. Supports layering an
optional strategy-specific override file on top of the default config,
e.g. backend/config/strategies/ny_am_htf_continuation_v1.1.yaml, so that
parameter experiments (V1.0, V1.1, ...) don't require touching the base
default file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from backend.config.schema import AppConfig

DEFAULT_CONFIG_PATH = Path(__file__).parent / "default.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(
    path: Optional[Path] = None,
    override_path: Optional[Path] = None,
) -> AppConfig:
    """
    Load and validate configuration.

    :param path: base config file (defaults to backend/config/default.yaml)
    :param override_path: optional strategy-version override file, deep-merged on top
    """
    base_path = path or DEFAULT_CONFIG_PATH
    with open(base_path, "r") as f:
        raw = yaml.safe_load(f) or {}

    if override_path is not None:
        with open(override_path, "r") as f:
            override_raw = yaml.safe_load(f) or {}
        raw = _deep_merge(raw, override_raw)

    return AppConfig.model_validate(raw)
