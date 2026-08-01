# Provider Adapters Requirements

- **版本**：0.1.0
- **狀態**：Approved baseline；各 adapter slice 仍須遵守 `tasks.md` gate
- **Task authority**：`.kiro/specs/provider-adapters/tasks.md`
- **契約 authority**：`docs/architecture/schemas/**/contract.schema.json`

> Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 `SOURCE_TEXT_UNCERTAIN`。ADR-001..010 已核准；各 phase-specific blocking gate 仍然有效。

## 1. Scope

本 Spec 只涵蓋 Provider-owned adapter、provider mapper/error mapping/config、contract harness、opt-in provider integration tests 與 conformance evidence。Core Domain/Application/Presentation、Port/DTO、shared schema/assertions、Core fake composition、unit/architecture/e2e tests不在 Provider authority。

## 2. Common requirements

- **PA-REQ-001**：Adapter 應實作其對應的 `1.0.0` Port schema；完整 contract set 為十個 Port、37 個 stable methods，並宣告 contract/provider/service/model version。
- **PA-REQ-002**：Provider payload/SDK type/exception/credential 不得穿越 mapper 至 Application/Domain。
- **PA-REQ-003**：每個 operation 應遵守 schema 的 `x-method-policy`（完整 timeout、retry owner、idempotency、concurrency）與 method-specific `ErrorResult` union；unknown exception 必須安全映射為 `unexpected_provider_error`，不回 stack/vendor payload/secret。
- **PA-REQ-004**：跨 process 只接受六欄 `DeadlineDTO`；receiver 使用自己的 monotonic clock 建 local deadline，adapter 不延長 deadline、不 hidden unbounded retry。
- **PA-REQ-005**：Adapter 應遵守 schema 內 request/success/error、`additionalProperties`、replay 與 concurrency constraints，不得以 generic error 或 port-wide default 取代 operation-specific contract。
- **PA-REQ-006**：Provider 應以 adapter harness 執行 Core-owned shared contract assertions，包含 Evidence offset/token TTL、Reasoning citation graph 與 Artifact Manifest 跨欄語意；不得修改 shared schema/assertions/expected semantics。
- **PA-REQ-007**：真實 provider/AWS tests 只能放 `tests/integration/providers/`、顯式 opt-in，且不得由一般 CI 無意外呼。
- **PA-REQ-008**：Logs/events 不得含 secret、token、Authorization header、PII、完整 prompt/chain-of-thought/raw sensitive content 或 SDK dumps。
- **PA-REQ-009**：Port gap 應提交 `docs/proposals/PORT_CHANGE_<name>.md`，包含 Problem、Proposed change、Compatibility impact、Testing requirements、SemVer impact；未核准前不得改 Core contract。
- **PA-REQ-010**：Provider 只能修改 ADR-010 指定 capability paths；不得建立 provider-name alternate infrastructure directory。

## 3. Capability requirements

### Collectors / Web Grounding

- **PA-COL-001**：只執行 Planner 核准的 category/query/assets/range/priority/budget；Web Grounding 只發現 URL，不改 plan。
- **PA-COL-002**：依 ADR-007 proposed limits 執行 HTTPS/443、DNS/IP/SSRF、redirect、robots、allowlist、payload、timeout、per-host rate/concurrency guard。
- **PA-COL-003**：每 job 收斂 success/skipped/failed；RawRecord 帶 raw/clean hash、opaque locator、time、HTTP metadata、query provenance 與 security result。

### Evidence extraction

- **PA-EXT-001**：只接收 bounded cleaned content/locator 與核准 taxonomy/schema/policy；不得產生 Evidence/lineage/system IDs/time/hash/locator。
- **PA-EXT-002**：Adapter 不自行 repair loop；Core 最多要求一次 ≤20 秒 repair；invalid after repair 回 quarantined/typed failure。

### Market regime

- **PA-MR-001**：只接收 Core 計算且已驗證/對齊、帶 source refs/version/hash 的 canonical Decimal feature vector。
- **PA-MR-002**：Infer ≤25 秒、zero retry；response 符合 probability/anomaly/model/`input_feature_hash` schema。
- **PA-MR-003**：Adapter 不得生成 fallback probabilities、synthetic regime 或 Core `AnalysisResult`；失敗只回 typed error，由 Core 建立 deterministic fallback。

### Reasoning

- **PA-RSN-001**：只接收 bounded task-scoped Structured Reasoning Context；無 network/database/object-store/secret tools。
- **PA-RSN-002**：只輸出 schema-constrained Facts/Inferences/Conclusions/refs/limitations，不回 chain-of-thought/完整 prompt。
- **PA-RSN-003**：Adapter 不決定 primary repair/fallback branch；Core 控制 generate → one repair → fallback → validate。

### Persistence / events / artifacts / observability

- **PA-AWS-001**：Repository adapter 應維持 conditional/transactional invariants、append-only history、strong snapshot pagination 與 unknown-outcome replay；`ExecutionRepository.acquire_quota_and_create` 是 atomic revalidate + Task input lock + quota acquisition + Execution create + pass consume 的唯一 authority，`TaskRepository` 不提供獨立 execution lock。
- **PA-AWS-002**：Event delivery at-least-once，以 event ID dedup；redaction failure 不 fallback raw logging。
- **PA-AWS-003**：Artifact same key/hash no-op、different hash conflict；Manifest last；storage adapter 不產生內容或決定 publication outcome。
- **PA-AWS-004**：IAM least privilege、encryption、secret isolation、safe DLQ/redrive；redrive 仍受原 Execution deadline。

## 4. Acceptance authority

Provider completion 只由 `tasks.md` checkbox + evidence 決定；Core T70–T74 只是 reference。Shared contract/API behavior 的最終核准仍屬 Core Maintainer。Blocking decisions 只在 `docs/architecture/open-questions.md` 維護。
