"""Load observed-form captures for offline FormComposer regression (CF.5 / W.7)."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from magicapply.infrastructure.browser.forms.fields import FieldKind, FieldVariant, FormField
from magicapply.infrastructure.browser.forms.schema import FormSchema, SchemaSource

logger = logging.getLogger(__name__)

_CAPTURE_ROOT = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "captured"


@dataclass
class CaptureMeta:
    ats: str
    form_selectors: tuple[str, ...]
    schema_id: str
    source: SchemaSource = "scanned"
    step_id: str | None = None
    recipe: str | None = None
    allowed_unhandled: tuple[str, ...] = ()
    job_url: str = "https://example.com/jobs/1"
    live: bool = False


@dataclass
class CapturedFieldExpectation:
    label: str
    kind: FieldKind
    selector: str
    resolved_strategy: str
    required: bool = False
    variant: FieldVariant | None = None
    step_id: str | None = None
    widget_id: str | None = None
    options: list[str] = field(default_factory=list)


@dataclass
class CaptureBundle:
    capture_id: str
    dom_html: str
    app_id: str
    job_url: str
    meta: CaptureMeta
    fields: list[CapturedFieldExpectation]


def captured_fixtures_root() -> Path:
    return _CAPTURE_ROOT


def list_capture_dirs(root: Path | None = None) -> list[Path]:
    base = root or captured_fixtures_root()
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "dom.html").exists())


def load_capture(capture_dir: Path) -> CaptureBundle:
    """Load ``dom.html`` + ``form.yaml`` from a capture directory."""
    dom_path = capture_dir / "dom.html"
    form_path = capture_dir / "form.yaml"
    if not dom_path.exists():
        raise FileNotFoundError(f"missing dom.html in {capture_dir}")
    if not form_path.exists():
        raise FileNotFoundError(f"missing form.yaml in {capture_dir}")

    payload = yaml.safe_load(form_path.read_text()) or {}
    meta_raw = payload.get("capture") or {}
    selectors = meta_raw.get("form_selectors") or [meta_raw.get("form_selector", "form")]
    if isinstance(selectors, str):
        selectors = [selectors]

    meta = CaptureMeta(
        ats=str(meta_raw.get("ats", "unknown")),
        form_selectors=tuple(selectors),
        schema_id=str(meta_raw.get("schema_id", capture_dir.name)),
        source=meta_raw.get("source", "scanned"),
        step_id=meta_raw.get("step_id"),
        recipe=meta_raw.get("recipe"),
        allowed_unhandled=tuple(meta_raw.get("allowed_unhandled", ())),
        job_url=str(payload.get("job_url", meta_raw.get("job_url", "https://example.com/jobs/1"))),
        live=bool(meta_raw.get("live", False)),
    )

    fields = [_field_expectation(raw) for raw in payload.get("fields", [])]
    return CaptureBundle(
        capture_id=capture_dir.name,
        dom_html=dom_path.read_text(encoding="utf-8"),
        app_id=str(payload.get("app_id", capture_dir.name)),
        job_url=meta.job_url,
        meta=meta,
        fields=fields,
    )


def promote_observed_capture(
    observed_dir: Path,
    *,
    capture_id: str | None = None,
    dest_root: Path | None = None,
) -> Path:
    """Copy a live ``data/observed_forms/<ts>-<slug>/`` run into test fixtures."""
    if not (observed_dir / "dom.html").exists():
        raise FileNotFoundError(f"missing dom.html in {observed_dir}")
    if not (observed_dir / "form.yaml").exists():
        raise FileNotFoundError(f"missing form.yaml in {observed_dir}")

    dest = (dest_root or captured_fixtures_root()) / (capture_id or observed_dir.name)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(observed_dir, dest)
    return dest


def load_observed_capture(observed_dir: Path) -> CaptureBundle:
    """Load a live ``data/observed_forms/<ts>-<slug>/`` run (infer meta from URL)."""
    bundle = load_capture(observed_dir)
    if bundle.meta.ats != "unknown":
        return bundle

    host = observed_dir.name.split("-", 1)[-1].lower()
    ats = _ats_from_host(host)
    selectors = _default_selectors(ats)
    meta = CaptureMeta(
        ats=ats,
        form_selectors=selectors,
        schema_id=f"{ats}_observed",
        source="scanned",
        job_url=bundle.job_url,
    )
    return CaptureBundle(
        capture_id=bundle.capture_id,
        dom_html=bundle.dom_html,
        app_id=bundle.app_id,
        job_url=bundle.job_url,
        meta=meta,
        fields=bundle.fields,
    )


def schema_from_capture(bundle: CaptureBundle) -> FormSchema | None:
    """Build a recipe ``FormSchema`` when the capture declares one."""
    recipe = bundle.meta.recipe
    if not recipe:
        return None

    from magicapply.config.models import StaticAnswers
    from magicapply.infrastructure.browser.ats import workday_recipes

    answers = StaticAnswers(full_name="Fixture User", email="fixture@example.com")
    builders: dict[str, object] = {
        "workday_voluntary_disclosures": workday_recipes.voluntary_disclosures_schema,
        "workday_self_identify": workday_recipes.self_identify_schema,
    }
    builder = builders.get(recipe)
    if builder is None:
        logger.warning("unknown capture recipe %r", recipe)
        return None
    schema = builder(answers)
    return FormSchema(
        schema_id=bundle.meta.schema_id,
        ats=schema.ats,
        fields=schema.fields,
        form_selector=bundle.meta.form_selectors[0],
        source="recipe",
        step_id=bundle.meta.step_id or schema.step_id,
    )


def golden_strategies(bundle: CaptureBundle) -> dict[str, str]:
    """Map field label → expected ``resolved_strategy`` from the capture."""
    return {f.label: f.resolved_strategy for f in bundle.fields}


def form_field_from_expectation(exp: CapturedFieldExpectation) -> FormField:
    return FormField(
        selector=exp.selector,
        label=exp.label,
        kind=exp.kind,
        options=list(exp.options),
        required=exp.required,
        variant=exp.variant,
        step_id=exp.step_id,
        widget_id=exp.widget_id,
    )


def _field_expectation(raw: dict[str, Any]) -> CapturedFieldExpectation:
    return CapturedFieldExpectation(
        label=str(raw.get("label", "")),
        kind=raw.get("kind", "text"),
        selector=str(raw.get("selector", "")),
        resolved_strategy=str(raw.get("resolved_strategy", "unhandled")),
        required=bool(raw.get("required", False)),
        variant=raw.get("variant"),
        step_id=raw.get("step_id"),
        widget_id=raw.get("widget_id"),
        options=list(raw.get("options") or []),
    )


def _ats_from_host(host_slug: str) -> str:
    if "greenhouse" in host_slug:
        return "greenhouse"
    if "lever" in host_slug:
        return "lever"
    if "ashby" in host_slug:
        return "ashby"
    if "workday" in host_slug or "myworkday" in host_slug:
        return "workday"
    if host_slug.startswith("custom-") or "example-custom" in host_slug:
        return "generic"
    if "eightfold" in host_slug:
        return "eightfold"
    if "phenom" in host_slug:
        return "phenom"
    if "icims" in host_slug or "paramount" in host_slug:
        return "icims"
    if "netflix" in host_slug:
        return "netflix"
    if "hyatt" in host_slug:
        return "custom_careers"
    return "unknown"


def _default_selectors(ats: str) -> tuple[str, ...]:
    defaults: dict[str, tuple[str, ...]] = {
        "greenhouse": ("form#application-form", "form"),
        "lever": ("form.posting-form", "form"),
        "ashby": ("form",),
        "workday": ("[data-automation-id='applyFlowPage']", "form"),
        "generic": ("form#application-form", "form", "main form"),
        "eightfold": ("form#application-form", "form"),
        "phenom": ("form#application-form", "form"),
        "icims": ("form#application-form", "form.iCIMS_AppForm", "form"),
        "netflix": ("form#application-form", "form"),
        "custom_careers": ("form#application-form", "form"),
    }
    return defaults.get(ats, ("form",))