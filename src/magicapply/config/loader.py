"""YAML loading, cross-reference validation, and error reporting for configs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from magicapply.config.models import BaseConfig, KeywordBank, Profile, PromptsConfig

BASE_CONFIG_FILENAME = "base_config.yaml"
PROMPTS_FILENAME = "prompts.yaml"
KEYWORD_BANK_FILENAME = "keyword_bank.yaml"
PROFILES_DIRNAME = "profiles"


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or fails cross-validation."""


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """Result of loading a config root: the base + all profiles + prompts +
    (optional) global keyword bank.
    """

    base: BaseConfig
    profiles: dict[str, Profile]
    prompts: PromptsConfig
    keyword_bank: KeywordBank
    root: Path

    def resumes_dir(self) -> Path:
        return _resolve(self.root, self.base.paths.resumes_dir)

    def data_dir(self) -> Path:
        return _resolve(self.root, self.base.paths.data_dir)

    def profile(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ConfigError(f"unknown profile: {name!r}") from exc

    def effective_bank(self, profile: Profile) -> KeywordBank:
        """Global bank merged with the profile's optional override.

        Override entries win by ``term``; entries in the override that are
        new to the global bank get appended. When the profile has no
        override, returns the global bank unchanged.
        """
        if not profile.keyword_bank_override:
            return self.keyword_bank
        override_path = _resolve(self.root, profile.keyword_bank_override)
        override = _validate_keyword_bank(override_path)
        return self.keyword_bank.extend_with(override)


def load_config(root: Path) -> LoadedConfig:
    """Load base config + all profiles + prompts from the config root.

    Reads `<root>/base_config.yaml`, `<root>/profiles/*.yaml`, and
    `<root>/prompts.yaml`. Raises `ConfigError` with a helpful message on any
    structural or cross-reference failure.
    """
    root = root.expanduser().resolve()
    if not root.exists():
        raise ConfigError(f"config root does not exist: {root}")
    if not root.is_dir():
        raise ConfigError(f"config root is not a directory: {root}")

    base = _load_base(root)
    profiles = _load_profiles(root)
    prompts = _load_prompts(root)
    keyword_bank = _load_keyword_bank(root)
    _validate_cross_refs(base, profiles, root)
    return LoadedConfig(
        base=base,
        profiles=profiles,
        prompts=prompts,
        keyword_bank=keyword_bank,
        root=root,
    )


def _load_base(root: Path) -> BaseConfig:
    path = root / BASE_CONFIG_FILENAME
    if not path.exists():
        raise ConfigError(f"missing base config: {path}")
    raw = _read_yaml(path)
    try:
        return BaseConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid {path}:\n{exc}") from exc


def _load_prompts(root: Path) -> PromptsConfig:
    path = root / PROMPTS_FILENAME
    if not path.exists():
        raise ConfigError(f"missing prompts config: {path}")
    raw = _read_yaml(path)
    try:
        return PromptsConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid {path}:\n{exc}") from exc


def _load_keyword_bank(root: Path) -> KeywordBank:
    path = root / KEYWORD_BANK_FILENAME
    if not path.exists():
        # Banks are optional; a missing file yields an empty bank.
        return KeywordBank()
    return _validate_keyword_bank(path)


def _validate_keyword_bank(path: Path) -> KeywordBank:
    raw = _read_yaml(path)
    try:
        return KeywordBank.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid keyword bank {path}:\n{exc}") from exc


def _load_profiles(root: Path) -> dict[str, Profile]:
    profiles_dir = root / PROFILES_DIRNAME
    if not profiles_dir.exists():
        return {}

    out: dict[str, Profile] = {}
    for path in sorted(profiles_dir.glob("*.yaml")):
        raw = _read_yaml(path)
        try:
            profile = Profile.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"invalid {path}:\n{exc}") from exc

        if profile.name in out:
            raise ConfigError(
                f"duplicate profile name {profile.name!r} — defined in {profiles_dir}"
            )
        out[profile.name] = profile
    return out


def _validate_cross_refs(base: BaseConfig, profiles: dict[str, Profile], root: Path) -> None:
    source_names = base.source_names()
    resumes_dir = _resolve(root, base.paths.resumes_dir)

    for profile in profiles.values():
        unknown = [s for s in profile.sources if s not in source_names]
        if unknown:
            raise ConfigError(
                f"profile {profile.name!r} references unknown sources: {unknown}. "
                f"Known sources: {sorted(source_names)}"
            )

        resume_path = resumes_dir / profile.base_resume
        if not resume_path.exists():
            raise ConfigError(
                f"profile {profile.name!r} references missing base_resume: {resume_path}"
            )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {path}:\n{exc}") from exc

    if data is None:
        raise ConfigError(f"empty YAML file: {path}")
    if not isinstance(data, dict):
        raise ConfigError(f"expected top-level mapping in {path}, got {type(data).__name__}")
    return data


def _resolve(root: Path, relative_or_absolute: str) -> Path:
    p = Path(relative_or_absolute)
    if p.is_absolute():
        return p.resolve()
    return (root / p).resolve()
