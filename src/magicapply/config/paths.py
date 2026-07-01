"""Default filesystem locations for configuration."""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "MAGICAPPLY_HOME"


def config_home() -> Path:
    """Return the base directory for MagicApply state and configuration.

    Precedence:
        1. `MAGICAPPLY_HOME` env var, if set.
        2. `$XDG_CONFIG_HOME/magicapply` if `XDG_CONFIG_HOME` is set.
        3. `~/.config/magicapply` fallback.
    """
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return (Path(xdg) / "magicapply").expanduser().resolve()

    return (Path.home() / ".config" / "magicapply").resolve()


def default_config_root() -> Path:
    """The directory holding `base_config.yaml` and the `profiles/` subdir.

    Precedence:
        1. `./configs` in the current working directory if it exists.
        2. `config_home()` otherwise.
    """
    cwd_configs = Path.cwd() / "configs"
    if cwd_configs.exists():
        return cwd_configs.resolve()
    return config_home()
