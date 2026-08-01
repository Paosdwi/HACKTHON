# Core Ports and DTO Schemas

- **Contract set version**：`1.0.0`
- **狀態**：Approved
- **擁有者**：Core Platform / Application layer
- **Schema dialect**：JSON Schema Draft 2020-12
- **來源政策**：Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 `SOURCE_TEXT_UNCERTAIN`。

> 本文件是人類可讀索引與 traceability matrix。欄位、`required`、`additionalProperties`、enum、default、min/max、pattern、format、request/response union 與 examples 的唯一 machine-readable authority 是本文件連結的 `contract.schema.json`。ADR-001..010 與 contract set 1.0.0 已由 Maintainer 人工核准。

## 1. 共用邊界

共用 Draft 2020-12 schema：[`schemas/common/common.schema.json`](schemas/common/common.schema.json)

共用定義包含：

- `schema_version = 1.0.0`、opaque IDs、UTC RFC 3339 `Z`、UTC date、SHA-256、HTTPS/opaque URI。
- `CanonicalDecimal`、`NonNegativeDecimal`、`Probability`：依 ADR-004 使用 canonical decimal string；precision 38、scale 18；拒絕 scientific notation、`+`、leading zero、trailing fractional zero 與 `-0`。Master JSON number 只能在 migration/provider boundary 以 Decimal parser 轉換，不可先經 binary float。
- `DeadlineDTO`：`schema_version`、`operation_id`、`deadline_at_utc`、`budget_ms`、`sent_at_utc`、`safety_margin_ms`。跨 process 不傳或比較 monotonic epoch；receiver 以自己的 monotonic clock 建 local deadline，effective timeout 取 provider limit、budget 與 UTC remaining minus margin 的最小值。Step Functions、Lambda、HTTP 與 SDK timeout 是外層 guard，不可延長 Core deadline。
- `PortErrorDTO` 與 `ProviderHealthDTO`。

## 2. Port schema registry

每個 Port 目錄均包含 `contract.schema.json`、`valid-examples.json`、`invalid-examples.json`。合法與非法案例以同一離線 Draft 2020-12 registry 驗證。

| Port | Machine-readable contract | Methods | Stable contract IDs |
|---|---|---|---|
| TaskRepository | [`task_repository/contract.schema.json`](schemas/task_repository/contract.schema.json) | `consume_task_create_slot`, `create_or_get`, `get`, `consume_preflight_slot`, `append_preflight_result`, `get_latest_preflight` | `CT-TASK-CONSUME-CREATE-01`, `CT-TASK-CREATE-OR-GET-01`, `CT-TASK-GET-01`, `CT-TASK-CONSUME-PREFLIGHT-01`, `CT-TASK-APPEND-PREFLIGHT-01`, `CT-TASK-GET-PREFLIGHT-01` |
| ExecutionRepository | [`execution_repository/contract.schema.json`](schemas/execution_repository/contract.schema.json) | `acquire_quota_and_create`, `get`, `transition`, `record_manual_case`, `list_by_quota_scope` | `CT-EXEC-ACQUIRE-01`, `CT-EXEC-GET-01`, `CT-EXEC-TRANSITION-01`, `CT-EXEC-MANUAL-CASE-01`, `CT-EXEC-LIST-QUOTA-01` |
| EvidenceRepository | [`evidence_repository/contract.schema.json`](schemas/evidence_repository/contract.schema.json) | `append_evidence`, `append_claim_links`, `append_assessments`, `get`, `list_for_task`, `get_latest_assessments` | `CT-EVID-APPEND-01`, `CT-EVID-LINKS-01`, `CT-EVID-ASSESS-01`, `CT-EVID-GET-01`, `CT-EVID-LIST-01`, `CT-EVID-LATEST-01` |
| ArtifactRepository | [`artifact_repository/contract.schema.json`](schemas/artifact_repository/contract.schema.json) | `put`, `get`, `list_for_execution`, `put_manifest`, `get_manifest` | `CT-ART-PUT-01`, `CT-ART-GET-01`, `CT-ART-LIST-01`, `CT-ART-PUT-MANIFEST-01`, `CT-ART-GET-MANIFEST-01` |
| EventPublisher | [`event_publisher/contract.schema.json`](schemas/event_publisher/contract.schema.json) | `publish`, `publish_batch` | `CT-EVENT-PUBLISH-01`, `CT-EVENT-BATCH-01` |
| SourceCollector | [`source_collector/contract.schema.json`](schemas/source_collector/contract.schema.json) | `collect`, `health_check`, `capabilities` | `CT-COLLECT-COLLECT-01`, `CT-COLLECT-HEALTH-01`, `CT-COLLECT-CAPABILITIES-01` |
| EvidenceExtractor | [`evidence_extractor/contract.schema.json`](schemas/evidence_extractor/contract.schema.json) | `extract`, `repair`, `health_check` | `CT-EXTRACT-EXTRACT-01`, `CT-EXTRACT-REPAIR-01`, `CT-EXTRACT-HEALTH-01` |
| MarketRegimeProvider | [`market_regime_provider/contract.schema.json`](schemas/market_regime_provider/contract.schema.json) | `infer`, `health_check` | `CT-MARKET-INFER-01`, `CT-MARKET-HEALTH-01` |
| ReasoningProvider | [`reasoning_provider/contract.schema.json`](schemas/reasoning_provider/contract.schema.json) | `generate`, `repair`, `health_check` | `CT-REASON-GENERATE-01`, `CT-REASON-REPAIR-01`, `CT-REASON-HEALTH-01` |
| Clock | [`clock/contract.schema.json`](schemas/clock/contract.schema.json) | `now_utc`, `monotonic_ms` | `CT-CLOCK-UTC-01`, `CT-CLOCK-MONOTONIC-01` |

Examples：

- [`TaskRepository valid`](schemas/task_repository/valid-examples.json) / [`invalid`](schemas/task_repository/invalid-examples.json)
- [`ExecutionRepository valid`](schemas/execution_repository/valid-examples.json) / [`invalid`](schemas/execution_repository/invalid-examples.json)
- [`EvidenceRepository valid`](schemas/evidence_repository/valid-examples.json) / [`invalid`](schemas/evidence_repository/invalid-examples.json)
- [`ArtifactRepository valid`](schemas/artifact_repository/valid-examples.json) / [`invalid`](schemas/artifact_repository/invalid-examples.json)
- [`EventPublisher valid`](schemas/event_publisher/valid-examples.json) / [`invalid`](schemas/event_publisher/invalid-examples.json)
- [`SourceCollector valid`](schemas/source_collector/valid-examples.json) / [`invalid`](schemas/source_collector/invalid-examples.json)
- [`EvidenceExtractor valid`](schemas/evidence_extractor/valid-examples.json) / [`invalid`](schemas/evidence_extractor/invalid-examples.json)
- [`MarketRegimeProvider valid`](schemas/market_regime_provider/valid-examples.json) / [`invalid`](schemas/market_regime_provider/invalid-examples.json)
- [`ReasoningProvider valid`](schemas/reasoning_provider/valid-examples.json) / [`invalid`](schemas/reasoning_provider/invalid-examples.json)
- [`Clock valid`](schemas/clock/valid-examples.json) / [`invalid`](schemas/clock/invalid-examples.json)

## 3. Port behavior matrix

每個 operation 的 `x-method-policy` 是 machine-readable policy authority，必須完整指定 timeout、retry owner、idempotency 與 concurrency。每個 method 的 `ErrorResult` 是 method-specific union，不以 port-wide generic error list取代；adapter/provider 未知例外必須映射為 `unexpected_provider_error`，且不得暴露 raw exception、secret 或 vendor payload。Method-specific request/success/error definitions 位於各 schema `$defs`。

| Port | Timeout / retry owner | Idempotency / concurrency |
|---|---|---|
| TaskRepository | repository default 2s；Application retry；adapter zero hidden retry | operation replay；active user/fingerprint/24h window conditional create；preflight 3/minute；不提供獨立 execution lock |
| ExecutionRepository | repository default 2s；Application retry | `acquire_quota_and_create` 是唯一 atomic revalidate + Task input lock + quota acquisition + Execution create + pass consume authority；initial + admin rerun 最多 2 |
| EvidenceRepository | append/get 2s、list 5s；Application retry | append-only；same ID/same canonical payload no-op；different payload conflict；task-scoped strong snapshot pagination |
| ArtifactRepository | per file 5s；final write 20s target + 5s buffer；publication use case retry | same logical key/hash no-op；different hash conflict；Manifest last |
| EventPublisher | publish 2s、batch 3s；Application retry | at-least-once；event ID dedup；same ID/different payload conflict |
| SourceCollector | connect 3s、read 10s、static 15s、Playwright 30s；orchestrator retry owner | completed operation replay；new fetch requires new operation ID；per-host concurrency 2 |
| EvidenceExtractor | repair ≤20s once；Core retry/repair owner | operation + raw hash + model/schema/ruleset identify invocation；invalid after repair quarantined |
| MarketRegimeProvider | infer ≤25s、zero retry、5s Core fallback reserve | stateless inference；operation + feature hash + model identify invocation |
| ReasoningProvider | primary repair ≤60s once；Core controls fallback | operation + context hash + model role/version identify invocation；no provider tools/network/DB/secrets |
| Clock | local synchronous；no retry | reads side-effect free；monotonic only within `runtime_id`，never across process |

## 4. Evidence boundary

依 ADR-005 的 Maintainer Proposed Default：

- Canonical 欄位是 `raw_locator`；legacy S3-specific 欄位只能在 migration/adapter mapper 讀入並轉換，Core 不 dual-write。
- `ContentReferenceDTO.kind = quote|metric|document_section`，value 長度 1..4096；offset 是 optional half-open range。
- Web Evidence URL 必須 HTTPS；dataset Evidence URL 可為 `null`；`published_at` nullable、`fetched_at` required。
- raw/clean SHA-256、query provenance、task/execution/raw-record lineage 與 `validation_status=active|quarantined` 為 required。
- Assessment 以 per-evidence `assessment_sequence` append；latest 是最大 sequence，不以 timestamp 猜測。
- Pagination default 50、range 1..200，使用 task-scoped strong snapshot token + cursor。

## 5. Market regime fallback boundary

`MarketRegimeProvider` 只可回 schema-valid provider result 或 typed error：

- Adapter 不得自行補資料、產生 fallback probabilities、回傳 synthetic regime，或建立 Core `AnalysisResult`。
- timeout、unavailable 或 invalid distribution 後，Core 建立版本化 deterministic fallback `AnalysisResult`，保存公式／ruleset、source refs、quality、limitations，並揭露 provider absence。
- Probability 欄位各在 `[0,1]`；三者總和的程式語意 tolerance 是 `0.000001`。JSON Schema 驗證單欄位範圍；shared semantic contract test 驗證總和。

## 6. Artifact publication boundary

Normal bundle 目標維持全部格式。Degraded publication 的最低 bundle：

1. Final Report JSON
2. Evidence List JSON
3. Execution Log JSONL
4. Manifest JSON

缺任一最低項目不得 publish。Markdown/HTML/CSV renderer failure 可形成 `partial`；Manifest 最後寫入，列 available、missing、reason，且不自列。

## 7. Version 與變更治理

- `MAJOR`：刪除／改名欄位、改變既有語意或 enum、收緊造成合法 payload 失效。
- `MINOR`：新增 optional 欄位或相容 capability。
- `PATCH`：不改 wire behavior 的描述、範例或 validator 修正。
- Provider 不得直接修改 shared contract/schema/assertions。缺口使用 `docs/proposals/PORT_CHANGE_<name>.md`，包含 Problem、Proposed change、Compatibility impact、Testing requirements、SemVer impact，等待 Core Maintainer 決定。
- Adapter ownership 與唯一 task authority 見 [ADR-010](adr/ADR-010-collaboration-ownership.md) 及 `.kiro/specs/provider-adapters/tasks.md`。

## 8. Traceability

| Contract concern | Requirements | Decision / authority |
|---|---|---|
| Identity / tenant scope | CP-FR001、CP-FR011 | ADR-002 |
| Fingerprint / idempotency | CP-FR001 | ADR-001 |
| Distributed deadline | CP-FR011、CP-ARCH-05/08 | ADR-003 + common `DeadlineDTO` |
| Decimal | CP-FR007 | ADR-004 + common Decimal defs |
| Evidence / pagination / latest | CP-FR005 | ADR-005 + EvidenceRepository schema |
| Preflight atomic validity | CP-FR011 | ADR-006 + Task/Execution schemas |
| Collector security | CP-FR003/004 | ADR-007 + SourceCollector schema |
| Reasoning context | CP-FR009 | ADR-008 + ReasoningProvider schema |
| Artifact minimum | CP-FR010 | ADR-009 + ArtifactRepository schema |
| Ownership / change flow | CP-PORT-* | ADR-010 |

## 9. Validation evidence

本輪離線驗證使用 `jsonschema.Draft202012Validator.check_schema` 與 `referencing.Registry`：

- schemas：11（common + 十個 Port）
- Port contracts：10
- stable method contract IDs：37
- valid examples：37/37 accepted
- invalid examples：37/37 rejected
- failures：0

Shared semantic assertions 另驗證 JSON Schema 無法完整表達的 Evidence offset range/token TTL-expiry consistency、Reasoning citation/reference graph，以及 Artifact Manifest available/missing/reason/self-exclusion 跨欄 invariant。

此結果證明 Draft 2020-12 結構、examples 與 shared semantic assertions 一致；不會把任何 Proposed ADR 自動升為 Approved。`lock_for_execution`/`CT-TASK-LOCK-EXECUTION-01` 的移除是避免禁止的分步交易之 breaking draft change，仍待人工核准；OQ-B004 在 human schema review 前仍是 blocking。
