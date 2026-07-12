"""Apply pipeline types - shared between orchestrator and strategies.

This module breaks the circular import between apply.py and apply_strategies.py
by providing neutral ground for shared type definitions. Both modules can import
from here without creating a dependency cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from magicapply.domain.models.application import ApplicationState


@dataclass
class ApplyReport:
    """Result of one apply attempt.

    Records the outcome of attempting to submit an application, including:
    - Which application was processed (application_id)
    - What final state it reached (APPLIED, FAILED, NEEDS_INTERVENTION)
    - Any error message if the attempt failed

    Throttle deferrals are indicated by error strings starting with "throttle:"
    and are excluded from outcome counting (see apply_utils.is_counted_outcome).
    """

    application_id: str
    final_state: ApplicationState
    error: str | None = None
