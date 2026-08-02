"""View models, renderers, and typed safe error handling for Demo UI.

All rendering logic lives in presentation/demo_ui/ (T63 allowed directory).
Uses only Application-exported DTOs — never imports Infrastructure, Repository, or Provider.
"""

from __future__ import annotations

import html as html_mod
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from crypto_trust_agent.application.dto.repositories import ArtifactManifestDTO
from crypto_trust_agent.application.publication import (
    EvidenceListDTO,
    EvidenceListEntryDTO,
    ExecutionLogDTO,
    FinalReportDTO,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")
_ASSET_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_QUESTION_MAX_LENGTH = 2_000
_REDACTED_KEYS = re.compile(
    r"secret|token|jwt|password|authorization|prompt|raw_content",
    re.IGNORECASE,
)
_REDACTED_VALUES = re.compile(
    r"bearer\s+|authorization\s*:|jwt|token|secret|password",
    re.IGNORECASE,
)
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# ---------------------------------------------------------------------------
# Typed safe errors (never leak arbitrary messages)
# ---------------------------------------------------------------------------

_SAFE_ERROR_MAP: dict[str, tuple[int, str]] = {
    "validation_error": (422, "輸入資料無效，請檢查後再試一次。"),
    "unsupported_asset": (422, "不支援指定的幣種。"),
    "not_found": (404, "找不到要求的分析執行。"),
    "artifact_not_found": (404, "找不到要求的成果檔案。"),
    "artifact_hash_mismatch": (409, "成果檔案完整性驗證失敗。"),
    "artifact_size_mismatch": (409, "成果檔案大小驗證失敗。"),
    "preflight_not_ready": (409, "執行前檢查未通過，未建立正式分析。"),
    "dependency_unavailable": (503, "必要服務暫時無法使用。"),
    "rate_limited": (429, "要求次數已達上限，請稍後再試。"),
    "internal_error": (500, "系統暫時無法完成要求，請稍後再試。"),
    "unsafe_filename": (400, "要求的檔案名稱無效。"),
    "identity_mismatch": (403, "您無權存取這項資源。"),
}

_DISPLAY_CODE_MAP: dict[str, str] = {
    "completed": "已完成",
    "failed": "失敗",
    "success": "成功",
    "complete": "完整",
    "partial": "部分完成",
    "unavailable": "無法使用",
    "preflight_not_ready": "執行前檢查未通過",
    "degraded": "降級執行",
    "informational": "僅供參考",
    "supports": "支持",
    "contradicts": "矛盾",
    "context": "背景",
    "opposes": "反對",
}


def display_code(value: object) -> str:
    """Translate known display values while preserving their original wire code."""
    raw = str(value)
    translated = _DISPLAY_CODE_MAP.get(raw)
    return f"{translated}（{raw}）" if translated is not None else raw


class DemoUIError(Exception):
    """Typed safe error — only known codes allowed."""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_ERROR_MAP:
            code = "internal_error"
        self.code = code
        status, message = _SAFE_ERROR_MAP[code]
        self.status_code = status
        self.safe_message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DemoTaskInput:
    """Validated user input for a demo task submission."""

    question: str
    assets: tuple[str, ...]
    formal_run: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise DemoUIError("validation_error")
        if len(self.question) > _QUESTION_MAX_LENGTH:
            raise DemoUIError("validation_error")
        if not self.assets:
            raise DemoUIError("validation_error")
        for asset in self.assets:
            if not isinstance(asset, str) or not _ASSET_PATTERN.fullmatch(asset):
                raise DemoUIError("validation_error")
            if asset not in SUPPORTED_ASSETS:
                raise DemoUIError("unsupported_asset")
        if len(set(self.assets)) != len(self.assets):
            raise DemoUIError("validation_error")


# ---------------------------------------------------------------------------
# View models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DemoRunStatus:
    """Safe representation of formal run progress."""

    task_id: str
    execution_id: str | None
    state: str
    publication_outcome: str | None
    partial_reasons: tuple[str, ...]
    remaining_time_display: str | None
    artifact_count: int
    degradation_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DemoEvidenceView:
    """Safe evidence rendering view — no secrets, no raw content."""

    evidence_id: str
    source: str
    source_type: str
    source_url: str | None
    canonical_url: str
    fetched_at: str
    quote: str
    content_hash: str
    lineage: Mapping[str, object]
    assessment_id: str
    assessment_version: str
    assessment_sequence: int
    related_claims: tuple[Mapping[str, str], ...]


@dataclass(frozen=True, slots=True)
class DemoReportView:
    """Safe final report view for UI rendering."""

    task_id: str
    execution_id: str
    assets: tuple[str, ...]
    question: str
    market_judgment: str
    facts: tuple[Mapping[str, object], ...]
    inferences: tuple[Mapping[str, object], ...]
    conclusions: tuple[Mapping[str, object], ...]
    key_evidence: tuple[Mapping[str, object], ...]
    supporting_evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]
    contradictions: tuple[Mapping[str, object], ...]
    confidence: Mapping[str, object]
    limitations: tuple[str, ...]
    watchpoints: tuple[str, ...]
    publication_outcome: str
    renderer_failures: tuple[Mapping[str, str], ...]
    disclaimer: str


@dataclass(frozen=True, slots=True)
class DemoManifestView:
    """Safe manifest representation."""

    task_id: str
    execution_id: str
    publication_outcome: str
    available: tuple[Mapping[str, object], ...]
    missing: tuple[Mapping[str, object], ...]
    generated_at: str


@dataclass(frozen=True, slots=True)
class DemoLogEntryView:
    """Single sanitized execution log entry."""

    event_id: str
    timestamp: str
    step: str
    status: str
    duration_ms: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ArtifactDownloadResult:
    """Validated artifact download response."""

    content_bytes: bytes
    filename: str
    content_type: str


# ---------------------------------------------------------------------------
# Sanitization and escaping
# ---------------------------------------------------------------------------

def sanitize_value(value: Any) -> Any:
    """Recursively remove sensitive keys and redact suspicious values."""
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_value(item)
            for key, item in value.items()
            if not _REDACTED_KEYS.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str) and _REDACTED_VALUES.search(value):
        return "[REDACTED]"
    return value


def escape_html(value: str) -> str:
    """HTML-escape for safe rendering."""
    return html_mod.escape(value, quote=True)


def validate_safe_filename(filename: str) -> str:
    """Validate filename is safe (no path traversal, bounded length)."""
    if not filename or not _SAFE_FILENAME.fullmatch(filename):
        raise DemoUIError("unsafe_filename")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise DemoUIError("unsafe_filename")
    return filename


# ---------------------------------------------------------------------------
# View builders
# ---------------------------------------------------------------------------

def build_evidence_view(entry: EvidenceListEntryDTO) -> DemoEvidenceView:
    """Convert a canonical evidence list entry to a safe UI view."""
    content_ref = dict(entry.content_reference) if entry.content_reference else {}
    quote = str(content_ref.get("value", content_ref.get("quote", "")))[:4_096]
    lineage = sanitize_value(dict(entry.lineage))
    return DemoEvidenceView(
        evidence_id=entry.evidence_id,
        source=entry.source,
        source_type=entry.source_type,
        source_url=entry.source_url,
        canonical_url=entry.source_locator,
        fetched_at=str(entry.fetched_at),
        quote=quote,
        content_hash=entry.content_hash,
        lineage=lineage,
        assessment_id=entry.assessment_id,
        assessment_version=entry.assessment_version,
        assessment_sequence=entry.assessment_sequence,
        related_claims=entry.related_claims,
    )


def build_report_view(report: FinalReportDTO) -> DemoReportView:
    """Convert a canonical final report to a safe UI view."""
    return DemoReportView(
        task_id=report.task_id,
        execution_id=report.execution_id,
        assets=report.assets,
        question=report.question,
        market_judgment=report.market_judgment.statement,
        facts=tuple(
            {"fact_id": f.fact_id, "statement": f.statement, "evidence_refs": list(f.evidence_refs), "analysis_refs": list(f.analysis_refs)}
            for f in report.facts
        ),
        inferences=tuple(
            {"inference_id": i.inference_id, "statement": i.statement, "confidence": str(i.confidence), "fact_refs": list(i.fact_refs)}
            for i in report.inferences
        ),
        conclusions=tuple(
            {"conclusion_id": c.conclusion_id, "statement": c.statement, "confidence": str(c.confidence), "fact_refs": list(c.fact_refs), "inference_refs": list(c.inference_refs)}
            for c in report.conclusions
        ),
        key_evidence=tuple(
            {"evidence_id": k.evidence_id, "analysis_id": k.analysis_id, "explanation": k.explanation}
            for k in report.key_evidence
        ),
        supporting_evidence_ids=report.supporting_evidence_ids,
        counter_evidence_ids=report.counter_evidence_ids,
        contradictions=tuple(
            {"summary": c.summary, "severity": str(c.severity), "evidence_refs": list(c.evidence_refs)}
            for c in report.contradictions
        ),
        confidence=sanitize_value(report.confidence_components.to_wire()),
        limitations=report.limitations,
        watchpoints=report.watchpoints,
        publication_outcome=report.publication_outcome,
        renderer_failures=tuple(f.to_wire() for f in report.renderer_failures),
        disclaimer=report.disclaimer,
    )


def build_manifest_view(manifest: ArtifactManifestDTO) -> DemoManifestView:
    """Convert manifest DTO to safe UI view."""
    return DemoManifestView(
        task_id=manifest.task_id,
        execution_id=manifest.execution_id,
        publication_outcome=manifest.publication_outcome,
        available=tuple(manifest.available),
        missing=tuple(manifest.missing),
        generated_at=str(manifest.generated_at),
    )


def build_log_entry_views(log: ExecutionLogDTO) -> tuple[DemoLogEntryView, ...]:
    """Convert execution log to safe UI views."""
    views: list[DemoLogEntryView] = []
    for entry in log.entries:
        call = entry.call_summary or {}
        error_code: str | None = None
        if entry.safe_error is not None:
            error_code = str(entry.safe_error.get("code", "unknown"))
        views.append(
            DemoLogEntryView(
                event_id=entry.event_id,
                timestamp=str(entry.timestamp),
                step=entry.step,
                status=str(call.get("status", "unknown")),
                duration_ms=int(call.get("duration_ms", 0)),
                error_code=error_code,
            )
        )
    return tuple(views)


# ---------------------------------------------------------------------------
# HTML renderers
# ---------------------------------------------------------------------------

def render_evidence_html(views: Sequence[DemoEvidenceView]) -> str:
    """Render evidence list as safe HTML fragment."""
    support_count = sum(
        1 for view in views for claim in view.related_claims if claim.get("stance") == "supports"
    )
    contradiction_count = sum(
        1 for view in views for claim in view.related_claims if claim.get("stance") == "contradicts"
    )
    parts = [
        '<div class="artifact-shell evidence-list"><div class="report-intro">',
        '<span class="eyebrow">Evidence workspace</span><h1>證據清單</h1>',
        '<p class="helper">每張卡片保留來源、取得時間、引用內容、雜湊與資料血緣，讓判斷可以回到原始依據。</p></div>',
        '<div class="metric-grid">',
        f'<div class="metric-card"><div class="metric-label">證據筆數</div><div class="metric-value">{len(views)}</div></div>',
        f'<div class="metric-card"><div class="metric-label">支持關聯</div><div class="metric-value">{support_count}</div></div>',
        f'<div class="metric-card"><div class="metric-label">矛盾關聯</div><div class="metric-value">{contradiction_count}</div></div>',
        f'<div class="metric-card"><div class="metric-label">完成評估</div><div class="metric-value">{sum(1 for view in views if view.assessment_id)}</div></div>',
        '</div><p class="confidence-note"><strong>驗證方式：</strong>系統不把單篇新聞武斷標成絕對真或假；評估會結合來源可信度、時效性、獨立性、交叉一致性與反方證據。</p>',
        '<div class="evidence-grid">',
    ]
    for view in views:
        parts.append('<article class="evidence-item">')
        parts.append('<div class="evidence-top"><div>')
        parts.append(f'<span class="source-chip">{escape_html(view.source_type)}</span><h3>{escape_html(view.source)}</h3>')
        parts.append(f'<span class="helper">{escape_html(view.evidence_id)}</span></div>')
        parts.append('<span class="status-pill ok">來源已可追溯</span></div>')
        parts.append(f'<blockquote class="evidence-quote">{escape_html(view.quote)}</blockquote>')
        parts.append('<dl class="evidence-meta">')
        parts.append(f'<div><dt>來源</dt><dd>{escape_html(view.source)}</dd></div>')
        parts.append(f'<div><dt>來源類型</dt><dd>{escape_html(view.source_type)}</dd></div>')
        if view.source_url:
            parts.append(f'<div><dt>原始網址</dt><dd><a href="{escape_html(view.source_url)}" rel="noopener noreferrer">開啟來源 ↗</a></dd></div>')
        parts.append(f'<div><dt>取得時間</dt><dd>{escape_html(view.fetched_at)}</dd></div>')
        parts.append(f'<div><dt>驗證狀態</dt><dd>第 {view.assessment_sequence} 次評估完成</dd></div>')
        parts.append(f'<div><dt>評估版本</dt><dd>{escape_html(view.assessment_version)}</dd></div>')
        parts.append('</dl>')
        parts.append('<div class="details"><strong class="helper">相關主張</strong><div>')
        if view.related_claims:
            for claim in view.related_claims:
                claim_id = str(claim.get("claim_id", ""))
                raw_stance = str(claim.get("stance", "context"))
                stance = display_code(raw_stance)
                parts.append(f'<span class="stance-pill {escape_html(raw_stance)}">{escape_html(stance)}・{escape_html(claim_id)}</span> ')
        parts.append('</div></div>')
        parts.append('<details class="details"><summary>查看技術追溯資訊</summary><dl class="evidence-meta">')
        parts.append(f'<div><dt>標準化網址</dt><dd>{escape_html(view.canonical_url)}</dd></div>')
        parts.append(f'<div><dt>引用內容</dt><dd>{escape_html(view.quote)}</dd></div>')
        parts.append(f'<div><dt>內容雜湊</dt><dd><code>{escape_html(view.content_hash)}</code></dd></div>')
        parts.append(f'<div><dt>評估編號</dt><dd><code>{escape_html(view.assessment_id)}</code></dd></div>')
        lineage_json = json.dumps(sanitize_value(dict(view.lineage)), indent=2, ensure_ascii=False)
        parts.append(f'<div style="grid-column:1/-1"><dt>資料血緣</dt><dd><pre>{escape_html(lineage_json)}</pre></dd></div>')
        parts.append('</dl></details></article>')
    parts.append('</div></div>')
    return "\n".join(parts)


def render_report_html(view: DemoReportView) -> str:
    """Render final report as safe HTML fragment."""
    confidence_cards = (
        ("整體信心", view.confidence.get("overall")),
        ("證據品質", view.confidence.get("evidence_quality")),
        ("交叉一致性", view.confidence.get("consistency")),
        ("資料覆蓋度", view.confidence.get("coverage")),
    )
    parts = [
        '<div class="chat-layout"><section class="conversation">',
        '<header class="conversation-header"><div style="display:flex;align-items:center;gap:12px"><span class="agent-avatar">CT</span><div><h1>CryptoTrust 分析助理</h1><p>已完成證據導向分析</p></div></div>',
        f'<span class="status-pill ok">{escape_html(display_code(view.publication_outcome))}</span></header>',
        '<div class="message-list"><div><div class="message-meta">你</div><div class="message user"><div class="message-content">',
        f'<p><strong>問題：</strong> {escape_html(view.question)}</p></div></div></div>',
        '<div><div class="message-meta">CryptoTrust Agent</div><div class="message agent"><span class="agent-avatar">CT</span><div class="message-content">',
        '<p><strong>市場判斷</strong></p>',
        f'<p>{escape_html(view.market_judgment)}</p></div></div></div>',
        '<article class="panel final-report"><div class="report-intro"><span class="eyebrow">Final report</span><h1>最終分析報告</h1>',
        f'<p class="helper"><strong>幣種：</strong> {escape_html(", ".join(view.assets))} ・ <strong>產出狀態：</strong> {escape_html(display_code(view.publication_outcome))}</p></div>',
        '<div class="metric-grid">',
    ]
    for label, value in confidence_cards:
        percent, width = _confidence_percent(value)
        parts.append(
            f'<div class="metric-card"><div class="metric-label">{escape_html(label)}</div>'
            f'<div class="metric-value">{escape_html(percent)}</div><div class="meter" aria-label="{escape_html(label)} {escape_html(percent)}">'
            f'<span style="width:{width}%"></span></div></div>'
        )
    parts.append('</div><p class="confidence-note"><strong>如何解讀：</strong>可信度不是新聞真偽判決，而是根據證據品質、來源間一致程度與資料覆蓋範圍計算的分析信心。</p>')

    parts.append('<section class="report-section"><h2>關鍵事實</h2><ul>')
    for fact in view.facts:
        refs = ", ".join(list(fact.get("evidence_refs", [])) + list(fact.get("analysis_refs", [])))
        parts.append(f'<li>{escape_html(str(fact["statement"]))} <small>[{escape_html(refs)}]</small></li>')
    parts.append('</ul></section>')

    parts.append('<section class="report-section"><h2>推論</h2><ul>')
    for inf in view.inferences:
        inf_confidence, _ = _confidence_percent(inf.get("confidence"))
        parts.append(f'<li>{escape_html(str(inf["statement"]))} <span class="source-chip">信心 {escape_html(inf_confidence)}</span><br><small>事實：{escape_html(", ".join(inf.get("fact_refs", [])))}</small></li>')
    parts.append('</ul></section>')

    parts.append('<section class="report-section"><h2>結論</h2><ul>')
    for con in view.conclusions:
        conclusion_confidence, _ = _confidence_percent(con.get("confidence"))
        parts.append(f'<li>{escape_html(str(con["statement"]))} <span class="source-chip">信心 {escape_html(conclusion_confidence)}</span></li>')
    parts.append('</ul></section>')

    parts.append('<section class="report-section"><h2>矛盾訊號</h2>')
    if view.contradictions:
        parts.append('<ul>')
        for cont in view.contradictions:
            severity = display_code(cont.get("severity", ""))
            parts.append(f'<li><strong>{escape_html(severity)}</strong>：{escape_html(str(cont["summary"]))} [{escape_html(", ".join(cont.get("evidence_refs", [])))}]</li>')
        parts.append('</ul>')
    else:
        parts.append('<p>無</p>')
    parts.append('</section>')

    parts.append('<section class="report-section"><h2>信心說明</h2><p>以上數值來自正式 reasoning output 的 confidence components，未由 UI 自行推測。</p></section>')
    parts.append('<section class="report-section"><h2>已知限制</h2>')
    if view.limitations:
        parts.append('<ul>')
        for lim in view.limitations:
            parts.append(f'<li>{escape_html(lim)}</li>')
        parts.append('</ul>')
    else:
        parts.append('<p>無</p>')
    parts.append('</section>')

    parts.append('<section class="report-section"><h2>降級狀態</h2>')
    if view.renderer_failures:
        parts.append('<ul>')
        for rf in view.renderer_failures:
            reason = display_code(rf.get("reason_code", ""))
            parts.append(f'<li>{escape_html(str(rf.get("artifact_type", "")))}/{escape_html(str(rf.get("format", "")))}：{escape_html(reason)}</li>')
        parts.append('</ul>')
    else:
        parts.append('<p>無</p>')
    parts.append('</section>')

    if view.watchpoints:
        parts.append('<section class="report-section"><h2>觀察重點</h2><ul>')
        parts.extend(f'<li>{escape_html(item)}</li>' for item in view.watchpoints)
        parts.append('</ul></section>')

    disclaimer = (
        "本報告僅供參考，不構成投資建議。"
        if view.disclaimer == "This report is informational and is not investment advice."
        else view.disclaimer
    )
    parts.append(f'<p class="disclaimer">{escape_html(disclaimer)}</p>')
    parts.append('</article></div></section><aside class="insights-rail"><span class="eyebrow">Traceability</span><h2>證據摘要</h2>')
    parts.append(f'<div class="metric-card"><div class="metric-label">支持證據</div><div class="metric-value">{len(view.supporting_evidence_ids)}</div></div>')
    parts.append(f'<div class="metric-card" style="margin-top:10px"><div class="metric-label">反方證據</div><div class="metric-value">{len(view.counter_evidence_ids)}</div></div>')
    parts.append('<p class="rail-copy">每個事實、推論與結論保留 Evidence ID 或 Analysis ID，可從證據清單回查來源。</p>')
    status_href = (
        f'/demo/status?task_id={escape_html(view.task_id)}'
        f'&amp;execution_id={escape_html(view.execution_id)}'
    )
    parts.append(f'<a class="button full" href="{status_href}">返回成果列表</a></aside></div>')
    return "\n".join(parts)


def _confidence_percent(value: object) -> tuple[str, str]:
    """Format a canonical probability for display without inventing precision."""
    try:
        probability = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "—", "0"
    if not Decimal("0") <= probability <= Decimal("1"):
        return "—", "0"
    percentage = (probability * Decimal("100")).quantize(Decimal("0.1"))
    display = format(percentage, "f").rstrip("0").rstrip(".")
    width = format(percentage, "f")
    return f"{display}%", width


def render_log_html(entries: Sequence[DemoLogEntryView]) -> str:
    """Render execution log entries as safe HTML table."""
    parts = ['<h1>執行紀錄</h1><table class="execution-log"><thead><tr>']
    parts.append('<th>事件</th><th>時間</th><th>步驟</th><th>狀態</th><th>耗時（毫秒）</th><th>錯誤</th>')
    parts.append('</tr></thead><tbody>')
    for entry in entries:
        error = escape_html(display_code(entry.error_code)) if entry.error_code else ""
        status = escape_html(display_code(entry.status))
        parts.append(f'<tr><td>{escape_html(entry.event_id)}</td><td>{escape_html(entry.timestamp)}</td><td>{escape_html(entry.step)}</td><td>{status}</td><td>{entry.duration_ms}</td><td>{error}</td></tr>')
    parts.append('</tbody></table>')
    return "\n".join(parts)


def render_manifest_html(view: DemoManifestView) -> str:
    """Render manifest as safe HTML."""
    parts = ['<div class="manifest">']
    parts.append('<h1>成果清冊</h1>')
    parts.append(f'<p><strong>產出狀態：</strong> {escape_html(display_code(view.publication_outcome))}</p>')
    parts.append(f'<p><strong>產生時間：</strong> {escape_html(view.generated_at)}</p>')
    parts.append(f'<h2>可用成果（{len(view.available)}）</h2><ul>')
    for item in view.available:
        parts.append(f'<li>{escape_html(str(item.get("artifact_type", "")))}/{escape_html(str(item.get("format", "")))} — <code>{escape_html(str(item.get("sha256", "")))}</code></li>')
    parts.append('</ul>')
    parts.append(f'<h2>缺少成果（{len(view.missing)}）</h2>')
    if view.missing:
        parts.append('<ul>')
        for item in view.missing:
            reason = display_code(item.get("reason_code", item.get("reason", "")))
            parts.append(f'<li>{escape_html(str(item.get("artifact_type", "")))}/{escape_html(str(item.get("format", "")))} — {escape_html(reason)}</li>')
        parts.append('</ul>')
    else:
        parts.append('<p>無</p>')
    parts.append('</div>')
    return "\n".join(parts)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def build_report_document_view(payload: Mapping[str, object]) -> DemoReportView:
    """Build a safe report view from an integrity-checked Application document."""
    judgment = _mapping(payload.get("market_judgment"))
    confidence = sanitize_value(_mapping(payload.get("confidence_components")))
    return DemoReportView(
        task_id=str(payload.get("task_id", "")),
        execution_id=str(payload.get("execution_id", "")),
        assets=tuple(str(item) for item in payload.get("assets", ()) if isinstance(item, str)),
        question=str(payload.get("question", ""))[:2_000],
        market_judgment=str(judgment.get("statement", ""))[:2_000],
        facts=tuple(sanitize_value(item) for item in _mapping_items(payload.get("facts"))),
        inferences=tuple(sanitize_value(item) for item in _mapping_items(payload.get("inferences"))),
        conclusions=tuple(sanitize_value(item) for item in _mapping_items(payload.get("conclusions"))),
        key_evidence=tuple(sanitize_value(item) for item in _mapping_items(payload.get("key_evidence"))),
        supporting_evidence_ids=tuple(str(item) for item in payload.get("supporting_evidence_ids", ()) if isinstance(item, str)),
        counter_evidence_ids=tuple(str(item) for item in payload.get("counter_evidence_ids", ()) if isinstance(item, str)),
        contradictions=tuple(sanitize_value(item) for item in _mapping_items(payload.get("contradictions"))),
        confidence=confidence if isinstance(confidence, Mapping) else {},
        limitations=tuple(str(item)[:512] for item in payload.get("limitations", ()) if isinstance(item, str)),
        watchpoints=tuple(str(item)[:512] for item in payload.get("watchpoints", ()) if isinstance(item, str)),
        publication_outcome=str(payload.get("publication_outcome", "")),
        renderer_failures=tuple(sanitize_value(item) for item in _mapping_items(payload.get("renderer_failures"))),
        disclaimer=str(payload.get("disclaimer", ""))[:512],
    )


def build_evidence_document_views(payload: Mapping[str, object]) -> tuple[DemoEvidenceView, ...]:
    """Build safe Evidence views from an integrity-checked Application document."""
    views: list[DemoEvidenceView] = []
    for item in _mapping_items(payload.get("items")):
        content_reference = _mapping(item.get("content_reference"))
        quote = str(content_reference.get("value", content_reference.get("quote", "")))[:4_096]
        lineage = sanitize_value(_mapping(item.get("lineage")))
        claims = tuple(
            {"claim_id": str(claim.get("claim_id", "")), "stance": str(claim.get("stance", ""))}
            for claim in _mapping_items(item.get("related_claims"))
        )
        views.append(
            DemoEvidenceView(
                evidence_id=str(item.get("evidence_id", "")),
                source=str(item.get("source", "")),
                source_type=str(item.get("source_type", "")),
                source_url=str(item["source_url"]) if isinstance(item.get("source_url"), str) else None,
                canonical_url=str(item.get("source_locator", "")),
                fetched_at=str(item.get("fetched_at", "")),
                quote=quote,
                content_hash=str(item.get("content_hash", "")),
                lineage=lineage if isinstance(lineage, Mapping) else {},
                assessment_id=str(item.get("assessment_id", "")),
                assessment_version=str(item.get("assessment_version", "")),
                assessment_sequence=(
                    item.get("assessment_sequence")
                    if type(item.get("assessment_sequence")) is int and item.get("assessment_sequence", 0) >= 1
                    else 1
                ),
                related_claims=claims,
            )
        )
    return tuple(views)


def build_log_document_views(payload: Mapping[str, object]) -> tuple[DemoLogEntryView, ...]:
    """Build safe execution-log rows from integrity-checked JSONL entries."""
    views: list[DemoLogEntryView] = []
    for entry in _mapping_items(payload.get("entries")):
        call = _mapping(entry.get("call_summary"))
        safe_error = _mapping(entry.get("safe_error"))
        duration = call.get("duration_ms", 0)
        views.append(
            DemoLogEntryView(
                event_id=str(entry.get("event_id", "")),
                timestamp=str(entry.get("timestamp", "")),
                step=str(entry.get("step", "")),
                status=str(call.get("status", "unknown")),
                duration_ms=duration if type(duration) is int and duration >= 0 else 0,
                error_code=str(safe_error.get("code")) if safe_error.get("code") is not None else None,
            )
        )
    return tuple(views)


def build_manifest_document_view(payload: Mapping[str, object]) -> DemoManifestView:
    """Build a safe manifest view from an integrity-checked Application document."""
    return DemoManifestView(
        task_id=str(payload.get("task_id", "")),
        execution_id=str(payload.get("execution_id", "")),
        publication_outcome=str(payload.get("publication_outcome", "")),
        available=tuple(sanitize_value(item) for item in _mapping_items(payload.get("available"))),
        missing=tuple(sanitize_value(item) for item in _mapping_items(payload.get("missing"))),
        generated_at=str(payload.get("generated_at", "")),
    )


def render_error_html(error: DemoUIError) -> str:
    """Render a typed safe error page — never includes arbitrary messages."""
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>發生錯誤</title></head><body>'
        f'<h1>發生錯誤</h1><p><strong>錯誤代碼：</strong> {escape_html(display_code(error.code))}</p>'
        f'<p>{escape_html(error.safe_message)}</p>'
        '<p><a href="/">返回示範首頁</a></p>'
        '</body></html>'
    )


__all__ = (
    "ArtifactDownloadResult",
    "DemoEvidenceView",
    "DemoLogEntryView",
    "DemoManifestView",
    "DemoReportView",
    "DemoRunStatus",
    "DemoTaskInput",
    "DemoUIError",
    "SUPPORTED_ASSETS",
    "build_evidence_view",
    "build_log_entry_views",
    "build_manifest_view",
    "build_report_view",
    "display_code",
    "escape_html",
    "render_error_html",
    "render_evidence_html",
    "render_log_html",
    "render_manifest_html",
    "render_report_html",
    "sanitize_value",
    "validate_safe_filename",
)
