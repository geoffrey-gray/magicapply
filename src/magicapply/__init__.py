"""MagicApply — config-driven job application automation."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("magicapply")
except PackageNotFoundError:  # editable install without metadata (shouldn't happen with hatchling)
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
