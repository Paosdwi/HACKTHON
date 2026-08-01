# ADR-005: Evidence Locator, Content Reference, and Repository Semantics

## Status

Approved — 2026-08-01

## Context

Master Evidence JSON 使用 `raw_content_s3_uri` 與 string `content_reference`，同時驗收文字使用一般化 raw locator。Core 不應綁定 S3，且 latest assessment/pagination/idempotency 需要明確語意。以下 v1 schema 選擇均為 **Maintainer Proposed Default**。

## Decision

1. v1 canonical Evidence/RawRecord 欄位使用 `raw_locator`，core schema 不使用 `raw_content_s3_uri`。Legacy 欄位只能由 migration/adapter mapper 接受並映射，不得由 core dual-write。
2. `ContentReferenceDTO` 為：

```json
{
  "kind": "quote",
  "value": "bounded verifiable excerpt or metric",
  "offset": {"start": 0, "end": 42},
  "unit": "unicode_scalar"
}
```

`kind` 只允許 `quote|metric|document_section`；`value` 長度為 1..4096 Unicode scalars；`offset` 與 `unit` 都是 optional，但若提供 offset，必須是 nonnegative half-open range，且 unit 必須是已知 enum。
3. Web evidence 的 `source_url` 與 `canonical_url` 必須是 HTTPS。Dataset evidence 的兩欄可為 `null`，由 dataset/file locator 與 lineage 識別。
4. `published_at` nullable；`fetched_at` 必填且為 UTC RFC3339。
5. 必須保存 SHA-256 raw `content_hash` 與 cleaned-content `clean_content_hash`，皆用 `sha256:<64 lowercase hex>`。
6. 必須保存 query provenance，以及 `task_id`、`execution_id`、`raw_record_id` lineage。`validation_status` 只允許 `active|quarantined`；quarantined 不得進 Reasoning Context/Final Report。
7. `EvidenceAssessment` append-only，新增 per-evidence integer `assessment_sequence`；latest 是同一 `evidence_id` 下合格 assessment 的最大 sequence，不以 timestamp/version lexical order決定。Sequence allocation 必須原子且冪等。
8. Evidence list page default `50`、minimum `1`、maximum `200`。查詢必須 task-scoped，使用 strong snapshot token 與 opaque cursor；token/cursor 不得跨 task 或 filter set 重用。
9. Evidence、link、assessment append 以各自 ID 冪等：相同 ID+相同 canonical payload 為成功 no-op；相同 ID+不同 payload 為 integrity conflict，絕不覆寫。

## Alternatives considered

- 保留 `raw_content_s3_uri` 為 core canonical：把 S3 transport 洩漏到 Domain/Application。
- 同時 canonical dual fields：容易分歧。
- `content_reference` 純 string：無法區分 quote/metric/section 或一致驗證 offset。
- latest by `computed_at`：clock collision/skew 造成不確定。
- eventual cursor 無 snapshot：concurrent append 可能 duplicate/omit。
- upsert assessment：破壞歷史與 audit。

## Consequences

Core locator 對 storage backend 中立，Evidence 引用更可驗證；repository 必須支援 per-evidence sequence 與 snapshot pagination。Strong snapshot 可能增加 storage/query 成本；dataset evidence 需要明確 file locator，而不是虛構 HTTPS URL。

## Compatibility impact

Legacy `raw_content_s3_uri` 與 string content reference 只能在 migration/adapter boundary 轉換。若 legacy string 無 kind，mapper 必須使用明確 migration default 並記 migration version，不得猜 offset。刪除 legacy core 欄位應在 v1 freeze 前完成。

## Security impact

HTTPS-only web locators、opaque raw locator、hash 與 lineage 降低 source substitution。Locator 不等於下載授權，不得把 signed credential/query secret 存入 Evidence。Task-scoped token/cursor 必須完整性保護且拒絕跨 tenant 重放。

## Testing requirements

- canonical/legacy locator mapping、legacy core rejection 與 no dual-write tests。
- ContentReference kind/value 1/4096/4097、Unicode scalar、offset/unit validation tests。
- HTTPS web URL、dataset null URL、published nullable/fetched required tests。
- raw/clean SHA-256、lineage/query provenance/status schema tests。
- concurrent sequence allocation、latest=max sequence、append-only/idempotency conflict tests。
- page limits、stable strong snapshot、cursor tamper/cross-task/cross-filter rejection tests。
- quarantined reference exclusion from context/report tests。

## Open Questions

- Strong snapshot token 的 TTL/cryptographic format 是 repository implementation contract，需 Port review。
- Offset `unit` 的完整 enum（例如 `unicode_scalar|byte|row|cell`）需 schema validation 時凍結。
- Dataset locator grammar 與 file/row lineage 需 Live Data schema 對齊。

## Source confidence

Master Evidence JSON/FR-005 可直接確認核心欄位與 append history intent；`raw_locator`、ContentReferenceDTO、sequence/latest、pagination 與 idempotency 的精確細節是 architecture design inference，均屬 **Maintainer Proposed Default**。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
