"""Demo UI presentation module — local-only artifact viewer using fake adapters."""

from crypto_trust_agent.presentation.demo_ui.app import DemoApp, DemoDownloadResult, DemoPageResult
from crypto_trust_agent.presentation.demo_ui.views import (
    DemoEvidenceView,
    DemoLogEntryView,
    DemoManifestView,
    DemoReportView,
    DemoRunStatus,
    DemoUIError,
    SUPPORTED_ASSETS,
)

__all__ = (
    "DemoApp",
    "DemoDownloadResult",
    "DemoEvidenceView",
    "DemoLogEntryView",
    "DemoManifestView",
    "DemoPageResult",
    "DemoReportView",
    "DemoRunStatus",
    "DemoUIError",
    "SUPPORTED_ASSETS",
)
