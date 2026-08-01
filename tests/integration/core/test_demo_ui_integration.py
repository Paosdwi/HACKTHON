"""Core integration coverage for Demo UI boundaries, rendering, and safety."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from crypto_trust_agent.application.use_cases.demo_ui import DemoUseCaseError  # noqa: E402
from crypto_trust_agent.presentation.api.demo_ui_api import create_local_demo_fastapi_app  # noqa: E402
from crypto_trust_agent.presentation.api.demo_ui_composition import DEMO_USER_TOKEN  # noqa: E402
from crypto_trust_agent.presentation.demo_ui.app import DemoApp  # noqa: E402
from crypto_trust_agent.presentation.demo_ui.views import (  # noqa: E402
    DemoUIError,
    escape_html,
    render_error_html,
    sanitize_value,
    validate_safe_filename,
)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEMO_USER_TOKEN}"}


def submit(
    client: TestClient,
    question: str = "請分析 BTC 目前的市場狀況，列出關鍵證據、信心與已知限制。",
) -> dict[str, object]:
    response = client.post(
        "/demo/submit",
        json={
            "question": question,
            "assets": ["BTC"],
            "timeframe_start": "2026-07-18T00:00:00Z",
            "timeframe_end": "2026-08-01T00:00:00Z",
        },
        headers=auth(),
    )
    if response.status_code != 201:
        raise AssertionError(response.text)
    return response.json()


def identity_params(result: dict[str, object]) -> dict[str, str]:
    parsed = parse_qs(urlsplit(str(result["status_url"])).query)
    return {
        "task_id": parsed["task_id"][0],
        "execution_id": parsed["execution_id"][0],
    }


class ArchitectureBoundaryTests(unittest.TestCase):
    """Concrete fake wiring is allowed only in the explicit composition root."""

    def test_demo_application_and_rendering_modules_do_not_import_concrete_adapters(self) -> None:
        modules = (
            "crypto_trust_agent.application.use_cases.demo_ui",
            "crypto_trust_agent.presentation.demo_ui.app",
            "crypto_trust_agent.presentation.demo_ui.views",
            "crypto_trust_agent.presentation.api.demo_ui_api",
        )
        for module_name in modules:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                source = Path(module.__file__).read_text(encoding="utf-8")
                for forbidden in (
                    "crypto_trust_agent.infrastructure",
                    "FakeArtifactRepository",
                    "FakePlatformStore",
                    "boto3",
                    "botocore",
                    "DynamoDB",
                    "S3Client",
                ):
                    self.assertNotIn(forbidden, source)

    def test_explicit_composition_root_contains_local_fakes_but_no_provider_or_aws_adapter(self) -> None:
        module = importlib.import_module("crypto_trust_agent.presentation.api.demo_ui_composition")
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertIn("crypto_trust_agent.infrastructure.fakes", source)
        self.assertIn("crypto_trust_agent.infrastructure.identity", source)
        for forbidden in (
            "infrastructure.aws",
            "infrastructure.collectors",
            "infrastructure.extraction",
            "infrastructure.reasoning",
            "infrastructure.market_regime",
            "boto3",
            "botocore",
        ):
            self.assertNotIn(forbidden, source)

    def test_demo_never_calls_publication_outside_composition_and_orchestrator(self) -> None:
        application = Path(
            importlib.import_module("crypto_trust_agent.application.use_cases.demo_ui").__file__
        ).read_text(encoding="utf-8")
        api = Path(
            importlib.import_module("crypto_trust_agent.presentation.api.demo_ui_api").__file__
        ).read_text(encoding="utf-8")
        self.assertNotIn("ArtifactPublicationService", application)
        self.assertNotIn(".publish(", application)
        self.assertNotIn("ArtifactPublicationService", api)
        self.assertNotIn(".publish(", api)


class TypedErrorSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        composition = create_local_demo_fastapi_app().state.demo_composition
        self.app = DemoApp(composition.use_case)

    def test_known_errors_render_only_fixed_safe_messages(self) -> None:
        expected_messages = {
            "not_found": "找不到要求的分析執行。",
            "validation_error": "輸入資料無效，請檢查後再試一次。",
            "internal_error": "系統暫時無法完成要求，請稍後再試。",
            "artifact_not_found": "找不到要求的成果檔案。",
            "preflight_not_ready": "執行前檢查未通過，未建立正式分析。",
        }
        for code, expected_message in expected_messages.items():
            with self.subTest(code=code):
                result = self.app.render_error(DemoUseCaseError(code))
                self.assertEqual(DemoUseCaseError(code).status_code, result.status_code)
                self.assertIn('<html lang="zh-Hant">', result.html_body)
                self.assertIn("發生錯誤", result.html_body)
                self.assertIn(expected_message, result.html_body)
                self.assertIn("返回示範首頁", result.html_body)
                for forbidden in (
                    "secret",
                    "jwt",
                    "bearer",
                    "password",
                    "traceback",
                    "stack",
                    "prompt",
                ):
                    self.assertNotIn(forbidden, result.html_body.casefold())

    def test_unknown_code_maps_to_internal_error(self) -> None:
        error = DemoUseCaseError("unknown-diagnostic-with-secret")
        self.assertEqual("internal_error", error.code)
        self.assertEqual(500, error.status_code)
        self.assertNotIn("unknown-diagnostic", self.app.render_error(error).html_body)


class ArtifactHtmlIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_local_demo_fastapi_app())
        cls.result = submit(cls.client)
        cls.params = identity_params(cls.result)

    def test_final_report_page_uses_structured_renderer(self) -> None:
        response = self.client.get("/demo/report", params=self.params, headers=auth())
        self.assertEqual(200, response.status_code)
        self.assertIn('<html lang="zh-Hant">', response.text)
        for label in (
            "最終分析報告",
            "幣種",
            "問題",
            "產出狀態",
            "市場判斷",
            "事實",
            "推論",
            "結論",
            "矛盾訊號",
            "信心說明",
            "已知限制",
            "降級狀態",
        ):
            self.assertIn(label, response.text)
        for narrative in (
            "訊號分歧",
            "中性評估",
            "本機 fake 資料",
            "本報告僅供參考，不構成投資建議。",
        ):
            self.assertIn(narrative, response.text)

    def test_evidence_page_contains_assessment_lineage_and_claims(self) -> None:
        response = self.client.get("/demo/evidence", params=self.params, headers=auth())
        self.assertEqual(200, response.status_code)
        for label in (
            "證據清單",
            "來源",
            "來源類型",
            "原始網址",
            "標準化網址",
            "取得時間",
            "引用內容",
            "內容雜湊",
            "資料血緣",
            "評估編號",
            "評估版本",
            "相關主張",
        ):
            self.assertIn(label, response.text)
        self.assertIn("ASSESS-", response.text)
        self.assertIn("CLAIM-", response.text)
        self.assertIn("raw_record_id", response.text)

    def test_execution_log_page_uses_sanitized_table(self) -> None:
        response = self.client.get("/demo/log", params=self.params, headers=auth())
        self.assertEqual(200, response.status_code)
        self.assertIn("執行紀錄", response.text)
        for heading in ("事件", "時間", "步驟", "狀態", "耗時（毫秒）", "錯誤"):
            self.assertIn(heading, response.text)
        self.assertIn("<table", response.text)
        self.assertIn("assessment", response.text)
        for forbidden in ("authorization", "bearer ", "raw_content", "password"):
            self.assertNotIn(forbidden, response.text.casefold())

    def test_manifest_page_lists_minimum_bundle_and_outcome(self) -> None:
        response = self.client.get("/demo/manifest", params=self.params, headers=auth())
        self.assertEqual(200, response.status_code)
        for label in ("成果清冊", "產出狀態", "產生時間", "可用成果", "缺少成果"):
            self.assertIn(label, response.text)
        self.assertIn("final_report/json", response.text)
        self.assertIn("evidence_list/json", response.text)
        self.assertIn("execution_log/jsonl", response.text)
        self.assertIn("完整（complete）", response.text)


class SecureDownloadIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_local_demo_fastapi_app())
        cls.result = submit(cls.client)
        cls.params = identity_params(cls.result)

    def test_four_minimum_artifacts_download_through_http_boundary(self) -> None:
        for artifact_type, artifact_format in (
            ("final_report", "json"),
            ("evidence_list", "json"),
            ("execution_log", "jsonl"),
            ("manifest", "json"),
        ):
            with self.subTest(artifact_type=artifact_type):
                response = self.client.get(
                    "/demo/download",
                    params={
                        **self.params,
                        "artifact_type": artifact_type,
                        "format": artifact_format,
                    },
                    headers=auth(),
                )
                self.assertEqual(200, response.status_code)
                self.assertTrue(response.content)
                filename = response.headers["content-disposition"]
                self.assertIn(artifact_type, filename)
                self.assertNotIn("..", filename)
                self.assertNotIn("/", filename)

    def test_unknown_missing_and_invalid_identity_fail_closed(self) -> None:
        requests = (
            {**self.params, "artifact_type": "secret_dump", "format": "bin"},
            {
                "task_id": "TASK-999",
                "execution_id": "EXEC-999",
                "artifact_type": "final_report",
                "format": "json",
            },
            {
                "task_id": "../etc",
                "execution_id": self.params["execution_id"],
                "artifact_type": "final_report",
                "format": "json",
            },
        )
        for params in requests:
            with self.subTest(params=params):
                response = self.client.get(
                    "/demo/download",
                    params=params,
                    headers=auth(),
                )
                self.assertIn(response.status_code, {404, 422})
                self.assertNotIn("traceback", response.text.casefold())


class ViewUtilitySafetyTests(unittest.TestCase):
    def test_escape_html_handles_tags_attributes_and_quotes(self) -> None:
        value = '<script data-x="1">alert(1)</script>'
        escaped = escape_html(value)
        self.assertNotIn("<script", escaped)
        self.assertIn("&lt;script", escaped)
        self.assertIn("&quot;", escaped)

    def test_safe_filename_rejects_traversal_and_accepts_generated_name(self) -> None:
        for filename in ("../../../etc/passwd", "..\\windows\\system32", ""):
            with self.subTest(filename=filename):
                with self.assertRaises(DemoUIError):
                    validate_safe_filename(filename)
        self.assertEqual(
            "TASK-001_EXEC-001_final_report.json",
            validate_safe_filename("TASK-001_EXEC-001_final_report.json"),
        )

    def test_sanitization_removes_sensitive_keys_and_redacts_values(self) -> None:
        result = sanitize_value(
            {
                "safe": "ok",
                "secret_key": "leaked",
                "jwt_data": "bad",
                "nested": {"authorization": "Bearer abc", "value": "token=abc"},
            }
        )
        self.assertEqual("ok", result["safe"])
        self.assertNotIn("secret_key", result)
        self.assertNotIn("jwt_data", result)
        self.assertNotIn("authorization", result["nested"])
        self.assertEqual("[REDACTED]", result["nested"]["value"])

    def test_render_error_never_includes_exception_text(self) -> None:
        html = render_error_html(DemoUIError("internal_error"))
        self.assertIn('<html lang="zh-Hant">', html)
        self.assertIn("發生錯誤", html)
        self.assertIn("返回示範首頁", html)
        self.assertNotIn("Traceback", html)
        self.assertNotIn("Exception", html)


if __name__ == "__main__":
    unittest.main()
