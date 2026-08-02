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
:root{color-scheme:light;--ink:#172033;--muted:#667085;--line:#e4e7ec;--panel:#fff;--soft:#f6f8fb;--brand:#3157d5;--brand-dark:#233fa5;--teal:#087e8b;--success:#067647;--warn:#b54708;--danger:#b42318;--shadow:0 18px 45px rgba(21,35,70,.08)}
*{box-sizing:border-box}body{font-family:Inter,"Noto Sans TC","Segoe UI",system-ui,sans-serif;margin:0;min-height:100vh;line-height:1.55;color:var(--ink);background:radial-gradient(circle at 8% 0,#edf1ff 0,transparent 28rem),#f7f8fb}
a{color:var(--brand)}.topbar{height:68px;border-bottom:1px solid rgba(228,231,236,.9);background:rgba(255,255,255,.88);backdrop-filter:blur(14px);display:flex;align-items:center;justify-content:space-between;padding:0 max(24px,calc((100vw - 1320px)/2));position:sticky;top:0;z-index:5}
.brand{display:flex;gap:12px;align-items:center;color:var(--ink);text-decoration:none;font-weight:760;letter-spacing:-.02em}.brand-mark{width:36px;height:36px;border-radius:12px;display:grid;place-items:center;color:#fff;background:linear-gradient(145deg,var(--brand),#6e4bd8);box-shadow:0 7px 18px rgba(49,87,213,.24)}
.environment-pill,.status-pill,.source-chip,.stance-pill{display:inline-flex;align-items:center;gap:7px;border-radius:999px;font-size:.78rem;font-weight:700}.environment-pill{padding:7px 11px;color:#344054;background:#f2f4f7;border:1px solid var(--line)}.environment-pill::before{content:"";width:7px;height:7px;border-radius:50%;background:#12b76a;box-shadow:0 0 0 3px #d1fadf}
main.app-shell{width:min(1320px,calc(100% - 32px));margin:30px auto 70px}.chat-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:24px;align-items:start}.conversation,.insights-rail,.panel,.artifact-shell{background:rgba(255,255,255,.96);border:1px solid var(--line);border-radius:22px;box-shadow:var(--shadow)}
.conversation{min-height:650px;display:flex;flex-direction:column;overflow:hidden}.conversation-header{padding:23px 26px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:14px}.conversation-header h1{font-size:1.05rem;margin:0}.conversation-header p{color:var(--muted);font-size:.82rem;margin:3px 0 0}.agent-avatar{flex:0 0 auto;width:42px;height:42px;border-radius:14px;display:grid;place-items:center;font-weight:800;color:#fff;background:linear-gradient(145deg,var(--brand),#6e4bd8)}
.message-list{padding:30px 28px;display:flex;flex:1;flex-direction:column;gap:22px}.message{display:grid;grid-template-columns:42px minmax(0,1fr);gap:12px;max-width:88%}.message.user{align-self:flex-end;grid-template-columns:minmax(0,1fr);max-width:76%}.message.user .message-content{color:#fff;background:linear-gradient(145deg,var(--brand),var(--brand-dark));border-radius:18px 18px 4px 18px}.message-content{background:var(--soft);border:1px solid #edf0f5;border-radius:4px 18px 18px;padding:16px 18px}.message-content p{margin:.25rem 0}.message-meta{font-size:.76rem;font-weight:700;color:var(--muted);margin:0 0 5px 54px}.message.user .message-meta{text-align:right;margin-right:3px;color:var(--muted)}
.composer{margin:0 20px 20px;border:1px solid #d0d5dd;border-radius:18px;background:#fff;box-shadow:0 8px 24px rgba(16,24,40,.06);padding:14px}.composer:focus-within{border-color:#91a8f6;box-shadow:0 0 0 4px #eef2ff}.composer label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}textarea,input{font:inherit}textarea{display:block;width:100%;min-height:92px;resize:vertical;border:0;outline:0;color:var(--ink);background:transparent;padding:4px}.composer-tools{border-top:1px solid #f0f1f3;margin-top:10px;padding-top:12px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
fieldset.assets{border:0;margin:0;padding:0}.assets legend{font-size:.74rem;color:var(--muted);margin-bottom:7px}.asset-options{display:flex;gap:7px;flex-wrap:wrap}.assets label{display:inline-flex;margin:0;position:relative}.assets input{position:absolute;opacity:0;pointer-events:none}.asset-chip{padding:6px 10px;border:1px solid var(--line);border-radius:9px;background:#fff;font-size:.78rem;font-weight:750;cursor:pointer}.assets input:checked+.asset-chip{color:var(--brand);background:#eef2ff;border-color:#a9b9f4}.assets input:focus-visible+.asset-chip{outline:3px solid #c7d2fe}
button,.button{display:inline-flex;align-items:center;justify-content:center;gap:7px;background:var(--brand);color:#fff;padding:10px 16px;border:0;border-radius:11px;text-decoration:none;font:inherit;font-size:.86rem;font-weight:740;cursor:pointer;transition:.18s ease;margin:3px}.button:hover,button:hover{background:var(--brand-dark);transform:translateY(-1px)}.button.secondary{color:#344054;background:#fff;border:1px solid #d0d5dd}.button.secondary:hover{background:#f9fafb}.button.full{width:100%;margin:4px 0}
.insights-rail{padding:22px;position:sticky;top:92px}.eyebrow{color:var(--brand);font-size:.73rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.insights-rail h2{font-size:1.05rem;margin:5px 0 7px}.rail-copy,.helper,.microcopy{color:var(--muted);font-size:.84rem}.capability-list{list-style:none;padding:0;margin:19px 0}.capability-list li{display:grid;grid-template-columns:32px 1fr;gap:10px;padding:12px 0;border-top:1px solid #f0f1f3}.capability-icon{width:30px;height:30px;display:grid;place-items:center;border-radius:9px;background:#eef2ff;color:var(--brand);font-weight:800}.capability-list strong{display:block;font-size:.86rem}.capability-list span{display:block;font-size:.76rem;color:var(--muted)}
.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin:18px 0}.metric-card{padding:15px;border:1px solid var(--line);border-radius:15px;background:#fff}.metric-label{font-size:.72rem;color:var(--muted);font-weight:700}.metric-value{font-size:1.52rem;font-weight:800;letter-spacing:-.04em;margin:3px 0}.meter{height:6px;border-radius:99px;background:#eaecf0;overflow:hidden;margin-top:8px}.meter>span{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--brand),#7b61dc)}
.panel,.artifact-shell{padding:24px;margin:0 0 20px}.panel h2,.artifact-shell h2{margin-top:0}.progress-list{list-style:none;padding:0;margin:18px 0}.progress-list li{display:grid;grid-template-columns:26px 1fr;gap:9px;padding:8px 0}.progress-dot{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;background:#dcfae6;color:var(--success);font-size:.72rem;font-weight:900}.status-pill{padding:6px 10px}.status-pill.ok{background:#ecfdf3;color:var(--success)}.status-pill.warn{background:#fffaeb;color:var(--warn)}.status-pill.error{background:#fef3f2;color:var(--danger)}
.artifact-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.artifact-actions .button{margin:0}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:12px;margin:28px 0 12px}.section-heading h2{margin:0}.section-heading span{color:var(--muted);font-size:.78rem}
.source-chip{padding:5px 9px;color:#175cd3;background:#eff8ff}.stance-pill{padding:5px 9px}.stance-pill.supports{background:#ecfdf3;color:#067647}.stance-pill.contradicts{background:#fef3f2;color:#b42318}.stance-pill.context{background:#f2f4f7;color:#475467}
code,pre{overflow-wrap:anywhere;white-space:pre-wrap}code{font-family:"Cascadia Code",Consolas,monospace;font-size:.8em}pre{padding:13px;background:#101828;color:#eaecf0;border-radius:12px}.evidence-list{display:grid;gap:16px}.evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.evidence-item{border:1px solid var(--line);border-radius:18px;padding:20px;background:#fff}.evidence-item h3{font-size:.97rem;margin:0}.evidence-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:15px}.evidence-quote{font-size:.94rem;margin:14px 0;padding:14px 15px;border-left:3px solid var(--brand);background:#f8f9fc;border-radius:0 12px 12px 0}.evidence-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.evidence-meta div{padding:10px;border-radius:11px;background:var(--soft)}.evidence-meta dt{font-size:.7rem;color:var(--muted)}.evidence-meta dd{margin:3px 0 0;font-size:.82rem;overflow-wrap:anywhere}.details{margin-top:13px;border-top:1px solid var(--line);padding-top:11px}.details summary{cursor:pointer;color:#475467;font-size:.8rem;font-weight:700}
.final-report h1,.evidence-list>h1,.manifest h1{font-size:1.45rem;margin:0 0 8px}.report-intro{margin-bottom:20px}.report-section{padding:2px 0 18px;border-bottom:1px solid var(--line)}.report-section:last-child{border-bottom:0}.report-section h2{font-size:1rem;margin:20px 0 9px}.report-section ul{padding-left:21px}.report-section li{margin:7px 0}.judgment{padding:18px;border-radius:16px;background:linear-gradient(145deg,#eef2ff,#f5f3ff);border:1px solid #d9ddff;font-size:1.04rem}.confidence-note{padding:11px 13px;border-radius:11px;background:#fffaeb;color:#7a2e0e;font-size:.8rem}.disclaimer{padding:14px;border-radius:12px;background:#f2f4f7;color:#475467;font-size:.82rem}
table{border-collapse:separate;border-spacing:0;width:100%;overflow:hidden;border:1px solid var(--line);border-radius:13px}th,td{border-bottom:1px solid var(--line);padding:11px;text-align:left;font-size:.8rem}th{background:#f9fafb;color:#475467}tr:last-child td{border-bottom:0}.ok{color:var(--success)}.warn{color:var(--warn)}.error{color:var(--danger)}
@media(max-width:900px){.chat-layout{grid-template-columns:1fr}.insights-rail{position:static}.metric-grid{grid-template-columns:repeat(2,1fr)}.evidence-grid{grid-template-columns:1fr}}@media(max-width:560px){.topbar{padding:0 16px}.environment-pill{display:none}main.app-shell{width:min(100% - 20px,1320px);margin-top:14px}.conversation{min-height:calc(100vh - 100px);border-radius:16px}.conversation-header,.message-list{padding:18px}.message{max-width:100%}.message.user{max-width:92%}.composer{margin:0 10px 10px}.composer-tools{align-items:stretch}.composer-tools button{width:100%}.metric-grid,.artifact-actions,.evidence-meta{grid-template-columns:1fr}}
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

    def __init__(self, use_case: DemoUseCase, *, live_mode: bool = False) -> None:
        self._use_case = use_case
        self._live_mode = live_mode

    def render_home(self) -> DemoPageResult:
        if self._live_mode:
            deployment_copy = (
                '<p class="microcopy">此公開 Demo 正在 AWS ECS Fargate 執行，會連線至真實公開市場與新聞來源。</p>'
                '<p class="microcopy">市場推理使用 Amazon Bedrock Claude；執行失敗時不會以假模型結果冒充。</p>'
            )
        else:
            deployment_copy = (
                '<p class="microcopy">此頁目前使用可重現的 Core 本機 fake adapters，不會連線至 AWS、外部 provider 或網路，也不需要任何 credential。</p>'
                '<p class="microcopy">正式部署時會由相同 Port 接上受控的 AWS provider adapters。</p>'
            )
        asset_inputs = "".join(
            f'<label><input type="checkbox" name="assets" value="{escape_html(asset)}"'
            f' checked><span class="asset-chip">{escape_html(asset)}</span></label>'
            for asset in SUPPORTED_ASSETS
        )
        body = (
            '<div class="chat-layout"><section class="conversation">'
            '<header class="conversation-header"><div style="display:flex;align-items:center;gap:12px">'
            '<span class="agent-avatar">CT</span><div><h1>CryptoTrust 分析助理</h1>'
            '<p>多來源市場研究・證據可回溯・不確定性透明</p></div></div>'
            '<span class="status-pill ok">準備就緒</span></header>'
            '<div class="message-list"><div><div class="message-meta">CryptoTrust Agent</div>'
            '<div class="message agent"><span class="agent-avatar">CT</span><div class="message-content">'
            '<p><strong>你好，我可以協助你分析加密資產。</strong></p>'
            '<p>我會整理市場資料、新聞證據與相互矛盾的訊號，清楚區分事實、推論及結論，並附上來源與信心說明。</p>'
            '</div></div></div></div>'
            '<form method="post" action="/demo/submit" class="composer">'
            '<label for="question"><strong>分析問題</strong></label>'
            '<textarea id="question" name="question" maxlength="2000" required>請分析所選幣種目前的市場狀況，整合價格走勢與主要新聞，逐一提出市場判斷、正反方證據、風險、不確定性、信心與後續觀察重點，並比較各幣種訊號的一致程度。</textarea>'
            '<input type="hidden" name="timeframe_start" value="2026-07-18T00:00:00Z">'
            '<input type="hidden" name="timeframe_end" value="2026-08-01T00:00:00Z">'
            '<div class="composer-tools">'
            f'<fieldset class="assets"><legend><strong>選擇幣種</strong></legend><div class="asset-options">{asset_inputs}</div></fieldset>'
            '<button type="submit">執行正式分析 <span aria-hidden="true">→</span></button></div></form>'
            '</section><aside class="insights-rail"><span class="eyebrow">分析透明度</span>'
            '<h2>每個判斷都有依據</h2><p class="rail-copy">回答完成後，你會看到以下可驗證資訊。</p>'
            '<ul class="capability-list">'
            '<li><span class="capability-icon">%</span><div><strong>證據品質</strong><span>顯示整體信心、一致性與覆蓋度</span></div></li>'
            '<li><span class="capability-icon">↗</span><div><strong>來源可追溯</strong><span>保留網址、取得時間、引用與內容雜湊</span></div></li>'
            '<li><span class="capability-icon">±</span><div><strong>正反訊號</strong><span>區分支持、矛盾與背景證據</span></div></li>'
            '<li><span class="capability-icon">!</span><div><strong>限制透明</strong><span>資料不足時明確降級，不硬給結論</span></div></li></ul>'
            f'{deployment_copy}'
            '</aside></div>'
        )
        title = "CryptoTrust Agent－AWS 公開示範" if self._live_mode else "CryptoTrust Agent－本機示範"
        return DemoPageResult(title, self._page(title, body))

    def render_status(self, status: DemoRunStatusDTO) -> DemoPageResult:
        state_class = "ok" if status.state == "completed" else "error" if status.state in {"failed", "preflight_not_ready"} else "warn"
        parts = [
            '<div class="chat-layout"><section class="conversation">',
            '<header class="conversation-header"><div><h1>分析進度</h1><p>Formal Run 狀態與成果發布</p></div>',
            f'<span class="status-pill {state_class}">{escape_html(display_code(status.state))}</span></header>',
            '<div class="message-list"><div><div class="message-meta">你</div><div class="message user"><div class="message-content">',
            f'<p>請開始正式分析。工作編號：<code>{escape_html(status.task_id)}</code></p></div></div></div>',
            '<div><div class="message-meta">CryptoTrust Agent</div><div class="message agent"><span class="agent-avatar">CT</span><div class="message-content">',
            '<p><strong>分析流程已完成。</strong></p>' if status.state == "completed" else '<p><strong>正在整理資料與分析結果。</strong></p>',
            f'<p>目前狀態：<span class="{state_class}">{escape_html(display_code(status.state))}</span></p>',
        ]
        if status.execution_id:
            parts.append(f'<p><strong>執行編號：</strong> <code>{escape_html(status.execution_id)}</code></p>')
        if status.remaining_seconds is not None:
            parts.append(f'<p><strong>剩餘時間：</strong> {status.remaining_seconds // 60}:{status.remaining_seconds % 60:02d}</p>')
        if status.terminal_outcome:
            parts.append(f'<p><strong>最終結果：</strong> {escape_html(display_code(status.terminal_outcome))}</p>')
        if status.publication_outcome:
            parts.append(f'<p><strong>成果發布狀態：</strong> {escape_html(display_code(status.publication_outcome))}</p>')
        parts.append('</div></div></div></div></section><aside class="insights-rail">')
        parts.append('<span class="eyebrow">Run summary</span><h2>執行摘要</h2>')
        parts.append(f'<div class="metric-card"><div class="metric-label">成果檔案數</div><div class="metric-value">{status.artifact_count}</div></div>')
        self._append_reasons(parts, "部分完成／執行前檢查原因", status.partial_reasons)
        self._append_reasons(parts, "降級原因", status.degradation_reasons)

        if status.execution_id and status.publication_outcome in {"complete", "partial"}:
            query = {"task_id": status.task_id, "execution_id": status.execution_id}
            parts.append('<section><div class="section-heading"><h2>查看完整回答</h2></div><div class="artifact-actions">')
            for action, label in (
                ("report", "最終分析報告"),
                ("evidence", "證據清單"),
                ("log", "執行紀錄"),
                ("manifest", "成果清冊"),
            ):
                href = f"/demo/{action}?{urlencode(query)}"
                parts.append(f'<a class="button secondary" href="{escape_html(href)}">{escape_html(label)}</a>')
            parts.append('</div></section><section><div class="section-heading"><h2>下載比賽四份成果檔案</h2></div><div class="artifact-actions">')
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
            parts.append("</div></section>")
        parts.append('<a class="button full" href="/">開始另一個分析</a></aside></div>')
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
            '<body><nav class="topbar"><a class="brand" href="/"><span class="brand-mark">CT</span>'
            '<span>CryptoTrust Agent</span></a><span class="environment-pill">可信分析工作區</span></nav>'
            f'<main class="app-shell">{body}</main></body></html>'
        )


__all__ = ("DemoApp", "DemoDownloadResult", "DemoPageResult")
