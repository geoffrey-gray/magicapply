"""Configuration models, loading, and path resolution.

Public surface:
    load_config, LoadedConfig, ConfigError
    BaseConfig, Profile, and their nested models.
"""

from magicapply.config.loader import ConfigError, LoadedConfig, load_config
from magicapply.config.models import (
    BaseConfig,
    CareerPageSource,
    JobUrlSource,
    LinkedInSource,
    LLMConfig,
    Paths,
    Profile,
    ScoringConfig,
    ScoringPrefilter,
    StaticAnswers,
)

__all__ = [
    "BaseConfig",
    "CareerPageSource",
    "ConfigError",
    "JobUrlSource",
    "LLMConfig",
    "LinkedInSource",
    "LoadedConfig",
    "Paths",
    "Profile",
    "ScoringConfig",
    "ScoringPrefilter",
    "StaticAnswers",
    "load_config",
]
