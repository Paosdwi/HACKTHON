"""Safe HTML facade for the local Demo UI."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from crypto_trust_agent.application.dto.repositories import ArtifactManifestDTO
from crypto_trust_agent.application.publication import (
    EvidenceListDTO,
    ExecutionLogDTO,
    FinalReportDTO,
)
from crypto_trust_agent.application.use_cases.demo_ui import (
    DemoArtifactDocumentDTO,
    DemoPrincipal,
    DemoRunStatusDTO,
    DemoUseCase,
    DemoUseCaseError,
    SUPPORTED_ASSETS,
)
from crypto_trust_agent.presentation.demo_ui.views import (
    DemoUIError,
    build_evidence_document_views,
    build_evidence_view,
    build_log_document_views,
    build_log_entry_views,
    build_manifest_document_view,
    build_manifest_view,
    build_report_document_view,
    build_report_view,
    display_code,
    escape_html,
    render_error_html,
    render_evidence_html,
    render_log_html,
    render_manifest_html,
    render_report_html,
)

_STYLE = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#172033}
main,article,.panel{border:1px solid #d9e0ea;border-radius:.6rem;padding:1rem;margin:1rem 0}
label{display:block;margin:.65rem 0}.assets label{display:inline-block;margin-right:1rem}
textarea,input{font:inherit}textarea{width:100%;min-height:7rem;box-sizing:border-box}
button,.button{display:inline-block;background:#175cd3;color:white;padding:.55rem .85rem;border:0;border-radius:.35rem;text-decoration:none;margin:.25rem .35rem .25rem 0}
.button.secondary{background:#475467}code,pre{overflow-wrap:anywhere;white-space:pre-wrap}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9e0ea;padding:.45rem;text-align:left}
.ok{color:#067647}.warn{color:#b54708}.error{color:#b42318}
""".strip()


@dataclass(frozen=True, slots=True)
class DemoPageResult:
    title: str
    html_body: str
    status_code: int = 200


@dataclass(frozen=True, slots=True)
class DemoDownloadResult:
    content_bytes: bytes
    filename: str
    content_type: str


class DemoApp:
    """Presentation facade.  All system reads go through DemoUseCase."""

    def __init__(self, use_case: DemoUseCase) -> None:
        self._use_case = use_case

    def render_home(self) -> DemoPageResult:
        asset_inputs = "".join(
            f'<label><input type="checkbox" name="assets" value="{escape_html(asset)}"'
            f'{" checked" if asset == "BTC" else ""}> {escape_html(asset)}</label>'
            for asset in SUPPORTED_ASSETS
        )
        body = (
            '<h1>CryptoTrust Agent－本機示範</h1>'
            '<p>此示範使用可重現的 Core 本機 fake adapters，不會連線至外部網路。選擇一個幣種進行市場狀況或假設驗證分析，或選擇兩個幣種進行比較。</p>'
            '<form method="post" action="/demo/submit" class="panel">'
            '<label for="question"><strong>分析問題</strong></label>'
            '<textarea id="question" name="question" maxlength="2000" required>請分析 BTC 目前的市場狀況，列出關鍵證據、信心與已知限制。</textarea>'
            f'<fieldset class="assets"><legend><strong>選擇幣種</strong></legend>{asset_inputs}</fieldset>'
            '<input type="hidden" name="timeframe_start" value="2026-07-18T00:00:00Z">'
            '<input type="hidden" name="timeframe_end" value="2026-08-01T00:00:00Z">'
            '<button type="submit">執行正式分析</button>'
            '</form>'
            '<p><small>此要求會在本機同步執行 deterministic fake 流程；不會連線至 AWS、外部 provider 或網路，也不需要任何 credential。</small></p>'
        )
        title = "CryptoTrust Agent－本機示範"
        return DemoPageResult(title, self._page(title, body))

    def render_status(self, status: DemoRunStatusDTO) -> DemoPageResult:
        state_class = "ok" if status.state == "completed" else "error" if status.state in {"failed", "preflight_not_ready"} else "warn"
        parts = [
            f'<h1>正式分析：{escape_html(status.task_id)}</h1>',
            f'<p><strong>狀態：</strong> <span class="{state_class}">{escape_html(display_code(status.state))}</span></p>',
        ]
        if status.execution_id:
            parts.append(f'<p><strong>執行編號：</strong> <code>{escape_html(status.execution_id)}</code></p>')
        if status.remaining_seconds is not None:
            parts.append(f'<p><strong>剩餘時間：</strong> {status.remaining_seconds // 60}:{status.remaining_seconds % 60:02d}</p>')
        if status.terminal_outcome:
            parts.append(f'<p><strong>最終結果：</strong> {escape_html(display_code(status.terminal_outcome))}</p>')
        if status.publication_outcome:
            parts.append(f'<p><strong>成果發布狀態：</strong> {escape_html(display_code(status.publication_outcome))}</p>')
        parts.append(f'<p><strong>成果檔案數：</strong> {status.artifact_count}</p>')
        self._append_reasons(parts, "部分完成／執行前檢查原因", status.partial_reasons)
        self._append_reasons(parts, "降級原因", status.degradation_reasons)

        if status.execution_id and status.publication_outcome in {"complete", "partial"}:
            query = {"task_id": status.task_id, "execution_id": status.execution_id}
            parts.append('<section class="panel"><h2>成果頁面</h2>')
            for action, label in (
                ("report", "最終分析報告"),
                ("evidence", "證據清單"),
                ("log", "執行紀錄"),
                ("manifest", "成果清冊"),
            ):
                href = f"/demo/{action}?{urlencode(query)}"
                parts.append(f'<a class="button" href="{escape_html(href)}">{escape_html(label)}</a>')
            parts.append('</section><section class="panel"><h2>下載最低成果組合</h2>')
            for artifact_type, artifact_format, label in (
                ("final_report", "json", "最終分析報告 JSON"),
                ("evidence_list", "json", "證據清單 JSON"),
                ("execution_log", "jsonl", "執行紀錄 JSONL"),
                ("manifest", "json", "成果清冊 JSON"),
            ):
                download_query = urlencode({**query, "artifact_type": artifact_type, "format": artifact_format})
                parts.append(
                    f'<a class="button secondary" download href="/demo/download?{escape_html(download_query)}">'
                    f'{escape_html(label)}</a>'
                )
            parts.append("</section>")
        parts.append('<p><a href="/">開始另一個分析</a></p>')
        title = "正式分析狀態"
        return DemoPageResult(title, self._page(title, "\n".join(parts)))

    def render_artifact(self, document: DemoArtifactDocumentDTO) -> DemoPageResult:
        if document.artifact_type == "final_report":
            view = build_report_document_view(document.payload)
            return DemoPageResult("最終分析報告", self._page("最終分析報告", render_report_html(view)))
        if document.artifact_type == "evidence_list":
            views = build_evidence_document_views(document.payload)
            return DemoPageResult("證據清單", self._page("證據清單", render_evidence_html(views)))
        if document.artifact_type == "execution_log":
            entries = build_log_document_views(document.payload)
            return DemoPageResult("執行紀錄", self._page("執行紀錄", render_log_html(entries)))
        if document.artifact_type == "manifest":
            view = build_manifest_document_view(document.payload)
            return DemoPageResult("成果清冊", self._page("成果清冊", render_manifest_html(view)))
        raise DemoUseCaseError("artifact_not_found")

    def render_report(self, report: FinalReportDTO) -> DemoPageResult:
        return DemoPageResult("最終分析報告", self._page("最終分析報告", render_report_html(build_report_view(report))))

    def render_evidence_list(self, evidence_list: EvidenceListDTO) -> DemoPageResult:
        views = tuple(build_evidence_view(item) for item in evidence_list.items)
        return DemoPageResult("證據清單", self._page("證據清單", render_evidence_html(views)))

    def render_execution_log(self, log: ExecutionLogDTO) -> DemoPageResult:
        return DemoPageResult("執行紀錄", self._page("執行紀錄", render_log_html(build_log_entry_views(log))))

    def render_manifest(self, manifest: object) -> DemoPageResult:
        if not isinstance(manifest, ArtifactManifestDTO):
            raise DemoUseCaseError("not_found")
        return DemoPageResult("成果清冊", self._page("成果清冊", render_manifest_html(build_manifest_view(manifest))))

    def render_error(self, error: DemoUseCaseError) -> DemoPageResult:
        result = render_error_html(DemoUIError(error.code))
        return DemoPageResult("發生錯誤", result, error.status_code)

    def download_artifact(
        self,
        *,
        principal: DemoPrincipal,
        task_id: str,
        execution_id: str,
        artifact_type: str,
        artifact_format: str,
    ) -> DemoDownloadResult:
        artifact = self._use_case.get_artifact(
            principal=principal,
            task_id=task_id,
            execution_id=execution_id,
            artifact_type=artifact_type,
            artifact_format=artifact_format,
        )
        return DemoDownloadResult(artifact.content_bytes, artifact.filename, artifact.content_type)

    @staticmethod
    def _append_reasons(parts: list[str], title: str, reasons: tuple[str, ...]) -> None:
        if not reasons:
            return
        parts.append(f'<h2>{escape_html(title)}</h2><ul>')
        parts.extend(f'<li>{escape_html(display_code(reason))}</li>' for reason in reasons)
        parts.append("</ul>")

    @staticmethod
    def _page(title: str, body: str) -> str:
        return (
            '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape_html(title)}</title><style>{_STYLE}</style></head>'
            f'<body><main>{body}</main></body></html>'
        )


__all__ = ("DemoApp", "DemoDownloadResult", "DemoPageResult")
