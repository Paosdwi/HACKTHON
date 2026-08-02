"""Explicitly opt-in live HTTPS collector check; skipped in normal CI."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "src" / "crypto_trust_agent" / "infrastructure"
import types
provider_package = types.ModuleType("pa70_infrastructure")
provider_package.__path__ = [str(INFRA)]
sys.modules.setdefault("pa70_infrastructure", provider_package)

from pa70_infrastructure.collectors.adapter import SecureSourceCollector

LIVE = os.getenv("PA70_LIVE_INTEGRATION") == "1"


@unittest.skipUnless(LIVE, "PA70 live integration requires explicit PA70_LIVE_INTEGRATION=1")
class LiveCollectorIntegration(unittest.TestCase):
    def test_allowlisted_public_https_record_and_replay(self):
        url = os.environ["PA70_LIVE_URL"]
        host = urlsplit(url).hostname
        self.assertEqual("https", urlsplit(url).scheme)
        self.assertTrue(host)
        now = datetime.now(timezone.utc)
        operation = "OP-PA70-LIVE-01"
        request = {
            "schema_version": "1.0.0", "operation_id": operation,
            "task_id": "TASK-PA70-LIVE", "execution_id": "EXEC-PA70-LIVE", "plan_job_id": "JOB-PA70-LIVE",
            "source_category": "official", "collection_mode": "static", "requirement": "optional",
            "assets": ["BTC"], "approved_query": "opt in collector conformance",
            "approved_urls": [url],
            "reporting_range": {"start": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"), "end": now.isoformat().replace("+00:00", "Z")},
            "priority": 100, "security_policy_version": "collector-security-1.0.0",
            "deadline": {
                "schema_version": "1.0.0", "operation_id": operation,
                "deadline_at_utc": (now + timedelta(seconds=15)).isoformat().replace("+00:00", "Z"),
                "budget_ms": 15000, "sent_at_utc": now.isoformat().replace("+00:00", "Z"),
                "safety_margin_ms": 1000,
            },
        }
        collector = SecureSourceCollector({host}, "live-opt-in-1.0.0")
        first = collector.collect(request)
        replay = collector.collect(request)
        self.assertEqual(first, replay)
        self.assertIn(first.get("outcome"), {"success", "failed"})
        self.assertEqual(0, first.get("retry_count"))


if __name__ == "__main__":
    unittest.main()
