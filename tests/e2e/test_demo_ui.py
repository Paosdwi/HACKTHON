"""HTTP E2E for the local Demo UI using real Core use cases and local fakes."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from crypto_trust_agent.application.orchestration.formal_run import FormalRunStep  # noqa: E402
from crypto_trust_agent.presentation.api.demo_ui_api import (  # noqa: E402
    create_local_demo_fastapi_app,
)
from crypto_trust_agent.presentation.api.demo_ui_composition import (  # noqa: E402
    DEMO_OTHER_USER_TOKEN,
    DEMO_USER_TOKEN,
)


def authorization(token: str = DEMO_USER_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def submission(
    question: str = "請分析 BTC 目前的市場狀況，列出關鍵證據、信心與已知限制。",
    assets: list[str] | None = None,
) -> dict[str, object]:
    return {
        "question": question,
        "assets": assets or ["BTC"],
        "timeframe_start": "2026-07-18T00:00:00Z",
        "timeframe_end": "2026-08-01T00:00:00Z",
    }


class FullLocalHttpFlowTests(unittest.TestCase):
    """The main flow begins at HTTP and has no pre-published fixture artifacts."""

    def setUp(self) -> None:
        self.fastapi_app = create_local_demo_fastapi_app()
        self.composition = self.fastapi_app.state.demo_composition
        self.client = TestClient(self.fastapi_app)

    def test_submit_runs_real_core_and_publishes_then_serves_every_page_and_download(self) -> None:
        self.assertEqual({}, self.composition.artifact_repository._items)
        self.assertEqual({}, self.composition.artifact_repository._manifests)
        self.assertEqual({}, self.composition.store.executions)

        submitted = self.client.post(
            "/demo/submit",
            json=submission(),
            headers=authorization(),
        )
        self.assertEqual(201, submitted.status_code, submitted.text)
        result = submitted.json()
        self.assertTrue(result["preflight_ready"])
        self.assertEqual("completed", result["state"])
        self.assertEqual("success", result["terminal_outcome"])
        self.assertEqual("complete", result["publication_outcome"])
        self.assertEqual(1, len(self.composition.store.tasks))
        self.assertEqual(1, len(self.composition.store.preflights[result["task_id"]]))
        self.assertEqual(1, len(self.composition.store.executions))
        self.assertEqual("completed", self.composition.store.executions[result["execution_id"]].state)
        self.assertEqual(6, len(self.composition.artifact_repository._items))
        self.assertEqual(1, len(self.composition.artifact_repository._manifests))
        self.assertEqual(
            7,
            len(self.composition.artifact_repository._items)
            + len(self.composition.artifact_repository._manifests),
        )
        self.assertEqual(1, self.composition.step_executor.call_count(FormalRunStep.ARTIFACT_PLACEHOLDER))

        status = self.client.get(result["status_url"], headers=authorization())
        self.assertEqual(200, status.status_code)
        self.assertIn('<html lang="zh-Hant">', status.text)
        self.assertIn("正式分析", status.text)
        self.assertIn("狀態", status.text)
        self.assertIn("已完成（completed）", status.text)
        self.assertIn("成功（success）", status.text)
        self.assertIn("完整（complete）", status.text)
        self.assertIn("最終分析報告", status.text)
        self.assertIn("證據清單", status.text)
        self.assertIn("執行紀錄", status.text)
        self.assertIn("成果清冊", status.text)
        self.assertIn("下載比賽四份成果檔案", status.text)
        self.assertEqual(4, status.text.count(" download "))

        parsed_status = urlsplit(result["status_url"])
        identity_query = parse_qs(parsed_status.query)
        params = {
            "task_id": identity_query["task_id"][0],
            "execution_id": identity_query["execution_id"][0],
        }
        pages = {
            "report": ("最終分析報告", "市場判斷", "已知限制"),
            "evidence": ("證據清單", "評估編號", "資料血緣"),
            "log": ("執行紀錄", "耗時（毫秒）", "錯誤"),
            "manifest": ("成果清冊", "可用成果", "完整（complete）"),
        }
        for action, markers in pages.items():
            with self.subTest(action=action):
                response = self.client.get(
                    f"/demo/{action}",
                    params=params,
                    headers=authorization(),
                )
                self.assertEqual(200, response.status_code, response.text)
                self.assertIn('<html lang="zh-Hant">', response.text)
                for marker in markers:
                    self.assertIn(marker, response.text)
                self.assertIn("default-src 'none'", response.headers["content-security-policy"])
                self.assertEqual("no-store", response.headers["cache-control"])

        downloads = (
            ("final_report", "json", "application/json"),
            ("evidence_list", "json", "application/json"),
            ("execution_log", "jsonl", "application/x-ndjson"),
            ("manifest", "json", "application/json"),
        )
        for artifact_type, artifact_format, content_type in downloads:
            with self.subTest(artifact_type=artifact_type):
                response = self.client.get(
                    "/demo/download",
                    params={
                        **params,
                        "artifact_type": artifact_type,
                        "format": artifact_format,
                    },
                    headers=authorization(),
                )
                self.assertEqual(200, response.status_code, response.text)
                self.assertTrue(response.content)
                self.assertTrue(response.headers["content-type"].startswith(content_type))
                disposition = response.headers["content-disposition"]
                self.assertNotIn("..", disposition)
                self.assertNotIn("/", disposition)
                self.assertIn(result["task_id"], disposition)

    def test_browser_home_form_uses_local_verified_session_and_redirects_to_status(self) -> None:
        home = self.client.get("/")
        self.assertEqual(200, home.status_code)
        self.assertIn('<html lang="zh-Hant">', home.text)
        self.assertIn('<meta charset="utf-8">', home.text)
        self.assertIn("CryptoTrust Agent－本機示範", home.text)
        self.assertIn("分析問題", home.text)
        self.assertIn("選擇幣種", home.text)
        self.assertIn("執行正式分析", home.text)
        self.assertIn('class="chat-layout"', home.text)
        self.assertIn("CryptoTrust 分析助理", home.text)
        self.assertIn("證據品質", home.text)
        self.assertIn("來源可追溯", home.text)
        self.assertIn("本機 fake", home.text)
        self.assertIn("不會連線至 AWS", home.text)
        self.assertIn(
            "請分析所選幣種目前的市場狀況，整合價格走勢與主要新聞",
            home.text,
        )
        self.assertIn("name=\"question\"", home.text)
        self.assertIn("name=\"assets\"", home.text)
        self.assertEqual(5, home.text.count('name="assets"'))
        self.assertEqual(5, home.text.count(' checked><span class="asset-chip">'))
        self.assertIn("crypto_trust_demo_session", self.client.cookies)

        submitted = self.client.post(
            "/demo/submit",
            data=submission("請分析 ETH 目前的市場狀況，列出關鍵證據、信心與已知限制。", ["ETH"]),
            follow_redirects=False,
        )
        self.assertEqual(303, submitted.status_code, submitted.text)
        self.assertTrue(submitted.headers["location"].startswith("/demo/status?"))
        status = self.client.get(submitted.headers["location"])
        self.assertEqual(200, status.status_code)
        self.assertIn("下載比賽四份成果檔案", status.text)
        self.assertIn("分析進度", status.text)
        self.assertIn("查看完整回答", status.text)


class OwnershipAndAuthenticationHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fastapi_app = create_local_demo_fastapi_app()
        self.client = TestClient(self.fastapi_app)
        response = self.client.post(
            "/demo/submit",
            json=submission(),
            headers=authorization(),
        )
        self.assertEqual(201, response.status_code)
        self.run = response.json()
        parsed = parse_qs(urlsplit(self.run["status_url"]).query)
        self.params = {
            "task_id": parsed["task_id"][0],
            "execution_id": parsed["execution_id"][0],
        }

    def test_missing_or_invalid_authentication_is_401(self) -> None:
        for headers in ({}, authorization("invalid-token")):
            with self.subTest(headers=headers):
                response = self.client.get(
                    "/demo/status",
                    params=self.params,
                    headers=headers,
                )
                self.assertEqual(401, response.status_code)
                self.assertIn('<html lang="zh-Hant">', response.text)
                self.assertIn("需要驗證身分", response.text)
                self.assertNotIn("traceback", response.text.casefold())

    def test_other_verified_principal_cannot_read_status_pages_or_downloads(self) -> None:
        other = authorization(DEMO_OTHER_USER_TOKEN)
        targets = (
            ("/demo/status", self.params),
            ("/demo/report", self.params),
            ("/demo/evidence", self.params),
            ("/demo/log", self.params),
            ("/demo/manifest", self.params),
            ("/demo/download", {**self.params, "artifact_type": "final_report", "format": "json"}),
        )
        for path, params in targets:
            with self.subTest(path=path):
                response = self.client.get(path, params=params, headers=other)
                self.assertEqual(404, response.status_code)
                self.assertNotIn("local-demo-user-subject", response.text)

    def test_identity_override_body_query_and_header_are_rejected(self) -> None:
        attacks = (
            {"json": submission() | {"sub": "attacker"}, "headers": authorization()},
            {"json": submission(), "headers": authorization() | {"X-User-Id": "attacker"}},
        )
        for attack in attacks:
            with self.subTest(attack=attack):
                response = self.client.post("/demo/submit", **attack)
                self.assertEqual(422, response.status_code)
        query_attack = self.client.get(
            "/demo/status",
            params={**self.params, "sub": "attacker"},
            headers=authorization(),
        )
        self.assertEqual(422, query_attack.status_code)


class PreflightAndSafetyHttpTests(unittest.TestCase):
    def test_unhealthy_preflight_never_creates_execution_or_artifact(self) -> None:
        app = create_local_demo_fastapi_app(
            dataset_status="unhealthy",
            dataset_reason_code="official_dataset_unavailable",
        )
        composition = app.state.demo_composition
        client = TestClient(app)
        self.assertEqual({}, composition.artifact_repository._items)
        self.assertEqual({}, composition.artifact_repository._manifests)

        response = client.post(
            "/demo/submit",
            json=submission(),
            headers=authorization(),
        )
        self.assertEqual(409, response.status_code, response.text)
        payload = response.json()
        self.assertFalse(payload["preflight_ready"])
        self.assertEqual("preflight_not_ready", payload["state"])
        self.assertIn("official_dataset_unavailable", payload["safe_reason_codes"])
        self.assertEqual({}, composition.store.executions)
        self.assertEqual({}, composition.store.quota)
        self.assertEqual({}, composition.artifact_repository._items)
        self.assertEqual({}, composition.artifact_repository._manifests)
        self.assertEqual((), composition.step_executor.calls)

    def test_report_escapes_untrusted_question_content(self) -> None:
        client = TestClient(create_local_demo_fastapi_app())
        question = 'Status of BTC <script>alert("x")</script>'
        submitted = client.post(
            "/demo/submit",
            json=submission(question),
            headers=authorization(),
        )
        self.assertEqual(201, submitted.status_code, submitted.text)
        parsed = parse_qs(urlsplit(submitted.json()["status_url"]).query)
        report = client.get(
            "/demo/report",
            params={"task_id": parsed["task_id"][0], "execution_id": parsed["execution_id"][0]},
            headers=authorization(),
        )
        self.assertEqual(200, report.status_code)
        self.assertNotIn('<script>alert("x")</script>', report.text)
        self.assertIn("&lt;script&gt;", report.text)

    def test_unknown_artifact_path_traversal_and_injected_filename_fail_safely(self) -> None:
        client = TestClient(create_local_demo_fastapi_app())
        submitted = client.post("/demo/submit", json=submission(), headers=authorization()).json()
        parsed = parse_qs(urlsplit(submitted["status_url"]).query)
        params = {"task_id": parsed["task_id"][0], "execution_id": parsed["execution_id"][0]}

        unknown = client.get(
            "/demo/download",
            params={**params, "artifact_type": "secret_dump", "format": "txt"},
            headers=authorization(),
        )
        traversal = client.get(
            "/demo/download",
            params={**params, "task_id": "../../../etc", "artifact_type": "final_report", "format": "json"},
            headers=authorization(),
        )
        injected = client.get(
            "/demo/download",
            params={
                **params,
                "artifact_type": "final_report",
                "format": "json",
                "filename": "../../../etc/passwd",
            },
            headers=authorization(),
        )
        self.assertEqual(404, unknown.status_code)
        self.assertEqual(422, traversal.status_code)
        self.assertEqual(200, injected.status_code)
        self.assertNotIn("passwd", injected.headers["content-disposition"])
        self.assertNotIn("..", injected.headers["content-disposition"])

    def test_malformed_json_error_does_not_leak_diagnostics(self) -> None:
        client = TestClient(create_local_demo_fastapi_app())
        response = client.post(
            "/demo/submit",
            content=b"not json{{{",
            headers={**authorization(), "Content-Type": "application/json"},
        )
        self.assertEqual(422, response.status_code)
        self.assertIn("輸入資料無效", response.text)
        lowered = response.text.casefold()
        for forbidden in ("traceback", "file ", "secret", "bearer", "password", "prompt"):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
