# External Integration Rules

- **版本**：`1.0.0`
- **狀態**：Approved
- **適用範圍**：Collectors、Web Grounding、Evidence extraction、Market regime、Reasoning、AWS persistence/event/artifact/observability、identity boundary、fake adapters
- **Port 索引**：`docs/architecture/ports-and-schemas.md`
- **欄位級 authority**：`docs/architecture/schemas/**/contract.schema.json`（Draft 2020-12）
- **決策 authority**：`docs/architecture/open-questions.md` + ADR-001..010

> Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 `SOURCE_TEXT_UNCERTAIN`。ADR-001..010 已由 Maintainer 人工核准；未來變更仍須依本文件的版本與提案流程辦理。

## 1. 核心原則

1. Core 擁有 Domain/Application、流程、Port/DTO、shared schema/assertions、fallback decision 與 publication validation。
2. Provider 擁有核准 capability path 內的 adapter、mapper、vendor error mapping、deadline/cancellation、provider config 與 conformance evidence。
3. Domain/Application 不 import FastAPI、Boto3、provider/AWS SDK、database、HTTP client 或 Infrastructure concrete class。
4. Planner、source requirement/query/budget、workflow branch、retry/fallback 次數、Execution outcome 與 artifact publication 由 Core 決定。
5. Production adapter 可由相同契約的 fake 替換；本機 E2E 不需 AWS/network/secret。

## 2. Adapter 接入流程

Provider 合併前必須：

1. 宣告 contract set、Port schema、provider/service/model versions。
2. 提供 Application DTO ↔ provider payload mapper；SDK object 不穿越。
3. 提供 vendor error → 該 method `ErrorResult` union 的 mapping、redaction 與 unknown exception → `unexpected_provider_error` handling。
4. 逐 operation 落實 `x-method-policy` 的完整 timeout、retry owner、idempotency 與 concurrency，並實作 distributed deadline/cancellation，證明零 hidden unbounded retry。
5. 實作契約指定的 replay/concurrency semantics，不得以 port-wide default 補缺。
6. 以 provider harness 跑 Core-owned shared contract suite，包含 security negative/fake parity，以及 Evidence offset/token TTL、Reasoning citation graph、Artifact Manifest 跨欄 semantic assertions。
7. 真實 integration tests 只放 `tests/integration/providers/`，顯式 opt-in。
8. 遇 Port gap 先走 proposal；不得先改 shared schema/DTO 或以 untyped extension/SDK type 繞過。

## 3. DTO、schema 與 Decimal

- Adapter 只接受已驗證 Application DTO，並防禦性驗 schema major、required fields、deadline、size。
- Provider response 進入 Application 前驗證 schema、enum、UTC、canonical decimal、hash/citation（適用時）。
- Core wire 只接受 ADR-004 canonical decimal string：precision 38、scale 18；拒絕 scientific notation、`+`、leading zero、trailing fractional zero、`-0`。
- Master/provider JSON number 只可在 mapper boundary 直接以 Decimal parser 轉 canonical string，不可先經 binary float；Core 不接受 string/number dual canonical format。
- return 可負；price/volume 非負；score/probability/anomaly/confidence ∈ `[0,1]`；probability sum tolerance `0.000001` 由 semantic contract test 驗證。
- Unknown major 拒絕；same-major compatible optional fields 可依 schema 處理。Schema、model、ruleset、planner、fingerprint versions 分開記錄。

## 4. Identity 與 authorization

依 ADR-002 提案：

- 驗證 Cognito JWT issuer、audience、signature、expiry，identity 使用 `sub`。
- 管理者須由 `cognito:groups` 的 `CryptoTrustAdmins` 取得，不接受自訂 admin header。
- Body/query/custom header/provider callback 不得 override identity。
- Repository command 帶 trusted scope；adapter 不從 ID 猜 tenant。
- Log/Event 只保存 versioned-key HMAC-SHA256 pseudonym，不保存 JWT、原 subject、email/username 或未遮罩 PII。

## 5. Distributed deadline、timeout 與 retry

### Wire contract

跨 process `DeadlineDTO` 固定欄位：

```json
{
  "schema_version": "1.0.0",
  "operation_id": "OP-001",
  "deadline_at_utc": "2026-08-01T02:15:00Z",
  "budget_ms": 25000,
  "sent_at_utc": "2026-08-01T02:14:35Z",
  "safety_margin_ms": 1000
}
```

- Caller monotonic clock 只在 caller runtime 使用，永不上 wire。
- Receiver 收到 DTO 後，以自己的 monotonic clock 建 local deadline。
- Effective timeout = minimum(provider timeout, budget remaining, UTC remaining − safety margin)。
- `safety_margin_ms` 提案 default 1000、min 100、max 5000；仍待 ADR-003 人工核准。
- Deadline 已到不啟動 I/O；取消後不得背景寫入無 lineage 資料。
- Step Functions、Lambda、HTTP client、SDK timeout 是 outer guards；不能延長 Core deadline。
- Application/orchestrator 是 retry owner；adapter 預設零 hidden retry。SDK 不能停用的 retry 必須固定上限、文件化、計入 attempt metadata 與 deadline。

明示上限：EvidenceExtractor repair ≤20 秒且一次；MarketRegime infer ≤25 秒且零 retry；Reasoning primary repair ≤60 秒且一次；Formal Run ≤900 秒。

## 6. Idempotency、delivery、preflight 與 concurrency

- Task scope：`(trusted_user_scope, request_fingerprint, active_24h_window)`；Formal quota scope 不含 Task window。
- Operation/entity ID、content hash、expected version 與 conditional write 用於 at-least-once delivery。
- Evidence/assessment/link/event append-idempotent；same ID/same canonical payload no-op，same ID/different payload conflict。
- Artifact same logical key/hash no-op，不同 hash conflict；Manifest last。
- Collector fetch 不假設 exactly-once；completed operation replay 回 recorded result，新 fetch 使用新 operation ID。
- Terminal state 不倒退。
- Pre-flight pass 提案綁 task/version/`input_lock_hash`/dependency snapshot，TTL 固定 60 秒且 single-use。`ExecutionRepository.acquire_quota_and_create` 是唯一 atomic revalidate + Task input lock + quota acquisition + Execution create + pass consume authority；`TaskRepository` 不提供獨立 lock operation。任何失敗、stale 或 expired pass 均不消耗 quota。

## 7. Evidence boundary

- Canonical Core 欄位是 `raw_locator`；legacy S3-specific locator 只在 migration/provider mapper 讀入，Core 不 dual-write。
- `ContentReferenceDTO.kind = quote|metric|document_section`；value 1..4096；offset optional half-open；unit optional。
- Web URL 必須 HTTPS；dataset URL 可 `null`；`published_at` nullable、`fetched_at` required。
- raw/clean SHA-256、query provenance、task/execution/raw-record lineage、active/quarantined status required。
- Assessment 每 Evidence 以 `assessment_sequence` append；latest 取最大 sequence，不以 timestamp 推定。
- Page default 50、min 1、max 200；task-scoped strong snapshot token/cursor，避免 duplicate/missing。
- Cross-task、missing lineage/reference、quarantined Evidence 不進 Reasoning Context/report。

## 8. Collector 與 Web Grounding

依 ADR-007 提案：HTTPS/443 only、URL≤2048 chars、redirect≤3 且每跳重驗；拒 private/loopback/link-local/reserved/multicast/metadata destination 與 URL credentials；connect 3s、read 10s、static total 15s、Playwright 30s；raw decompressed≤5MiB、cleaned≤1MiB；per-host concurrency 2、間隔≥1000ms；遵守 robots+allowlist。

Planner 固定 category/query/assets/range/priority/budget/requirement。Web Grounding 只發現 URL，不改 plan；URL 仍經自有 Collector 驗證/fetch/save。每個 job 收斂 success/skipped/failed；外部內容視為資料，不可成為 instruction。

## 9. EvidenceExtractor

- 只接收 bounded cleaned RawRecord/locator、assets、taxonomy、schema/policy、deadline。
- 可回 extracted claim/quote/classification，不可產生 fetched time、hash、locator、Evidence/link/assessment ID/version。
- Core 驗證 output 並決定一次 repair；adapter 不自行 repair loop/retry。Repair 失敗或逾時後 Core quarantine。
- Guardrails 不授予工具或 orchestration 權限；log 不保存完整 prompt/raw sensitive content/provider token。

## 10. MarketRegimeProvider

- 只接收 Core 已驗證、時間對齊且帶 source refs/version/hash 的 feature vector；adapter 不補值、不抓資料。
- Output 驗證 asset/as-of/window/model version/probabilities/anomaly/schema/`input_feature_hash`；三個 probability 的 semantic sum tolerance 是 `0.000001`。
- 單次含 cold start ≤25 秒、zero retry。
- Adapter 只能回 schema-valid provider result 或 typed error，**不得生成 fallback probabilities、synthetic regime 或 Core AnalysisResult**。
- Timeout/unavailable/invalid output 後，Core 建立版本化 deterministic fallback `AnalysisResult`，記 formula/ruleset、source refs、quality/limitations，揭露 provider absence。

## 10.1 LiveMarketDataProvider

- Production live extension 固定使用 Binance Spot public `GET /api/v3/klines`，只允許 BTC/ETH/SOL/BNB/XRP 對 USDT、`1d`、UTC closed candles。
- Adapter 直接以 Decimal 解析 provider numeric strings，禁止 binary float；source URL、fetch time、content hash、provider ruleset 與 `live_extension` provenance 必須保留。
- Core 以 `live-market-reconciliation-1.0.0` 驗證三天 overlap：OHLC 相對容許 1%，base-volume 25%。官方資料永遠優先，reconciliation failure 不得覆寫官方 rows。
- 2026-06-01 起才加入 live rows；missing/incomplete day 必須形成 limitation，禁止 forward-fill、插值或重用官方最後價格。
- Page timeout ≤10s、operation ≤30s、health/capabilities ≤3s；Core 是 retry owner，adapter attempt 1、hidden retry 0。
- Geographic/access denial 必須讓 pre-flight fail closed；secondary provider 需獨立 versioned proposal/ruleset。

## 11. ReasoningProvider

- 只接收 task-scoped、validated Structured Reasoning Context；禁止 network/database/object-store/secret tools 與 raw HTML。
- Bounds：canonical JSON≤524288 bytes、provider tokenizer≤64000 tokens、Evidence≤120、Analysis≤32、Contradiction≤64、Limitations≤50、question/excerpt≤2000 Unicode scalars。
- Core deterministic rank/truncate 並記 omissions；不得跨 Task 或帶 quarantined content。
- Output 分 Facts/Inferences/Conclusions 並由 Core 驗 citation/numeric graph；不回 chain-of-thought/完整 prompt。
- Core sequence：primary generate → 至多一次 primary repair（≤60s）→ fallback generate → validate。Adapter 不自行重試、循環或換模型；invalid fallback 不發布。

## 12. Persistence、queue、storage

- Table/index/key、bucket/object key、queue/service config 是 Infrastructure concern；Application 只見 Port DTO。
- Task create/rate、Formal quota/create、transition 需 conditional/transactional invariant；具體 resource transaction 仍由 OQ-B012 阻塞。
- Step Functions/SQS 假設 duplicate/out-of-order；redrive 仍受 original deadline，900 秒後不發布舊 Execution 報告。
- Raw/artifact 啟用 encryption、least privilege、integrity metadata；locator 對 Core opaque，client download short-lived/authorized。

## 13. Artifact publication

Normal bundle：Final Report JSON/Markdown/HTML、Evidence List JSON/CSV、Execution Log JSONL、Manifest JSON。

Degraded minimum：Final Report JSON + Evidence List JSON + Execution Log JSONL + Manifest JSON。缺任一不得 publish。Markdown/HTML/CSV renderer failure 可 partial。Manifest 最後寫，列 available/missing/reason，不自列。

Adapter 只儲存/讀取，不產生報告內容、不決定 Execution outcome。

## 14. Observability 與安全

必記 timestamp、task/execution、step、adapter、status、duration、retry count、safe parameters/summary、typed error、remaining deadline、fingerprint/ruleset outcome、assessment refs、admin rerun refs、URL allowlist outcome。

禁止 secret、API key、JWT、Authorization header、PII、原 identity、完整 prompt/chain-of-thought/raw sensitive content、SDK dumps、signed URL secret。Redaction failure 不得 fallback raw logging。

IAM least privilege；secret 使用 Secrets Manager/安全本機機制；production provider tests 顯式 opt-in；一般 CI 不外呼、不使用 production credential。

## 15. Ownership、task authority 與 Port change

Provider authority：

- `src/crypto_trust_agent/infrastructure/{collectors,extraction,reasoning,market_regime,persistence,events,artifacts,observability,aws}/`
- `tests/contract/` 的 adapter harness/fixtures/conformance evidence（不含 shared assertions）
- `tests/integration/providers/`
- `.kiro/specs/provider-adapters/`

Core authority：Domain、Application、Presentation、Infrastructure identity、Core fake composition、unit/architecture/e2e tests、`tests/integration/core/`、core-platform Spec、shared architecture contracts/schema/assertions。Core integration 不連真實 provider/AWS；真實 provider/AWS integration 只屬 `tests/integration/providers/`。

Provider adapter 工作唯一 task authority 是 `.kiro/specs/provider-adapters/tasks.md`。Core T70–T74 只有 cross-plan references，不含 checkbox/acceptance/completion authority，Core Kiro 不得執行。

Port gap proposal：`docs/proposals/PORT_CHANGE_<name>.md`，必含 Problem、Proposed change、Compatibility impact、Testing requirements、SemVer impact。等待 Core Maintainer；未核准前不得改 shared contract/schema/assertions。

## 16. Error 與降級責任

| Adapter failure | Adapter 回傳 | Core 決策 |
|---|---|---|
| timeout | typed timeout + safe metadata | 依 Port policy retry/fallback/partial |
| throttle | rate_limited + safe retry-after | 只在 deadline/policy 允許重試 |
| invalid payload | invalid_provider_output | repair/quarantine/Core fallback/reject |
| missing credential | unauthorized/not_configured | Pre-flight not ready |
| unsafe source | unsafe_source | 拒 fetch + safe audit |
| repository conflict | conflict + safe current version | re-read/resolve，不 blind overwrite |
| unknown exception | unexpected_provider_error | fail/degrade，不回 raw exception |

Adapter 不決定 `partial|failed|succeeded`。

## 17. Contract readiness evidence

每個 Provider task 完成前應在 provider-adapters authority 記錄：

- supported contract/schema/provider versions（完整 set 為十個 Port、37 stable methods、37 valid/37 invalid examples）
- mapper 無 SDK leak
- 逐 operation `x-method-policy`（timeout/retry/idempotency/concurrency）與 method-specific `ErrorResult`，含 unknown exception → `unexpected_provider_error`
- deadline/cancellation/zero hidden retry evidence
- idempotency/replay/concurrency evidence
- lightweight health probe
- security/IAM/network evidence
- shared contract results，含 Evidence offset/token TTL、Reasoning citation graph、Artifact Manifest 跨欄 semantic assertions
- opt-in integration results
- observability data-minimization evidence

## 18. Decision registry

Blocking OQ-B001..B015 與 nonblocking OQ-N001..N005 只在 `docs/architecture/open-questions.md` 維護。任何項目只可由列名 owner 人工決議；實作、測試、schema validation 或本文件存在不自動變更 status。
