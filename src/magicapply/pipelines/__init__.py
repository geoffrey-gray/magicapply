"""End-to-end workflows composed from domain + infrastructure.

Named `pipelines/` rather than the ARCHITECTURE.md `services/` — one file per
named end-to-end flow (discovery, apply). See docs/GOF_PATTERNS.md for the
"pipelines are Facades" note.
"""

from magicapply.pipelines.apply import ApplyPipeline, ApplyReport
from magicapply.pipelines.discovery import DiscoveryPipeline, DiscoveryReport

__all__ = [
    "ApplyPipeline",
    "ApplyReport",
    "DiscoveryPipeline",
    "DiscoveryReport",
]
