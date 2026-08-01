# CryptoTrust Agent — Technical Steering

## 技術與來源基線

- Python、Clean Architecture、依賴反轉；FastAPI 只在 Presentation／composition boundary。
- 正式 AWS 目標包含 Cognito、API Gateway、Lambda BFF、Step Functions、SQS、Lambda/ECS Fargate、S3、DynamoDB、Bedrock AgentCore、Nova、SageMaker AI、CloudWatch、Secrets Manager、CloudTrail。
- 外部 provider/AWS adapter 是可替換 Infrastructure 實作；Core 必須可用 fake adapters 完成本機 E2E。
- Master Spec 的來源信心依 `docs/architecture/source-confidence-register.md`；任何亂碼單獨推導標 `SOURCE_TEXT_UNCERTAIN`。
- ADR-001..010 已由 Maintainer 於 2026-08-01 人工核准；後續變更遵守 ADR 與 SemVer 流程。

## Clean Architecture

```text
Presentation/Infrastructure ──> Application ──> Domain
                                      │
                                      └──> Application-owned Ports
Infrastructure ──implements───────────┘
```

1. Domain 不 import FastAPI、Boto3、provider/AWS SDK、database 或 HTTP client。
2. Application 只依賴 Domain 與 Application-owned Port/DTO。
3. Infrastructure 隔離 serialization、network、persistence、queue、storage、provider SDK 與 exception mapping。
4. Presentation 驗證 identity、映射 HTTP，不包含 domain decision。
5. Composition root 是唯一 concrete adapter wiring 位置。

## Domain 與 Port 治理

必要模型：`Task`、`Execution`、immutable `Evidence`、`EvidenceClaimLink`、append-only `EvidenceAssessment`、`AnalysisResult`、planning/deadline/reasoning value objects。

Application 擁有並版本化：`TaskRepository`、`ExecutionRepository`、`EvidenceRepository`、`ArtifactRepository`、`EventPublisher`、`SourceCollector`、`EvidenceExtractor`、`MarketRegimeProvider`、`ReasoningProvider`、`Clock`。

人類索引：`docs/architecture/ports-and-schemas.md`。欄位級 authority：`docs/architecture/schemas/**/contract.schema.json`（JSON Schema Draft 2020-12）。十個 Port 共 37 個 stable methods；每個 operation 以 `x-method-policy` 完整定義 timeout、retry owner、idempotency 與 concurrency，並有 method-specific `ErrorResult` union。未知 adapter/provider exception 一律安全映射為 `unexpected_provider_error`，不得洩漏 vendor exception。

## Distributed deadline

- Formal Run hard deadline 是 900 秒且不可延長。
- 跨 process `DeadlineDTO` 固定為：`schema_version`、`operation_id`、`deadline_at_utc`、`budget_ms`、`sent_at_utc`、`safety_margin_ms`。
- 不把 monotonic 值放上 wire；receiver 以自己的 `Clock.monotonic_ms()` 建 local deadline。effective timeout 取 provider limit、remaining budget、UTC remaining minus margin 的最小值。
- `safety_margin_ms` 提案 default 1000、min 100、max 5000；仍待 ADR-003 人工核准。
- Step Functions、Lambda、HTTP client 與 SDK timeout 都是 outer guards；不得延長 Core deadline。Adapter 預設零 hidden retry，Application/orchestrator 是 retry owner。

## Decimal、Evidence 與 lineage

- Core wire 只接受 canonical decimal string；precision 38、scale 18；禁止 scientific notation、`+`、leading zero、trailing fractional zero、`-0`。Boundary 解析 Master/provider JSON number 時直接使用 Decimal parser，不可先經 binary float。
- Canonical Evidence locator 是 `raw_locator`；S3-specific legacy 欄位只在 migration/adapter mapper 讀入，不進 Core dual-write。
- `ContentReferenceDTO` 區分 `quote|metric|document_section`；Evidence 必備 raw/clean SHA-256、query provenance、task/execution/raw-record lineage 與 active/quarantined status。
- Assessment latest 取 per-evidence 最大 `assessment_sequence`；append-only。Evidence pagination 使用 task-scoped strong snapshot token/cursor，default 50、min 1、max 200。
- 跨 Task、missing lineage、missing reference 或 quarantined Evidence 不得進 Reasoning Context／report。

## Planner、preflight 與 concurrency

- Planner 不呼叫 LLM/provider；同 normalized input、ruleset、clock snapshot 產生 byte-equivalent canonical plan。
- Task create rate limit 先計數；fingerprint reuse 仍計數。Formal quota scope 不因 Task 輪替重置。
- Pre-flight pass 提案綁定 task/version/`input_lock_hash`/dependency snapshot，TTL 固定 60 秒、single-use。`ExecutionRepository.acquire_quota_and_create` 是唯一 atomic revalidate + Task input lock + quota acquisition + Execution create + pass consume authority；`TaskRepository` 不提供獨立 execution lock。失敗、stale 或 expired 不消耗 quota。
- 所有 write command 使用 operation/entity ID、hash 或 conditional version 以承受 at-least-once delivery。

## Provider 邊界

- SourceCollector 依 ADR-007 limits：HTTPS/443、URL≤2048、redirect≤3 每跳重驗、拒 private/loopback/link-local/reserved/multicast/metadata、connect 3s、read 10s、static 15s、Playwright 30s、raw≤5MiB、clean≤1MiB、per-host concurrency 2、interval≥1000ms。精確值仍待安全審核。
- EvidenceExtractor repair 最多一次、≤20 秒；再失敗 quarantine。
- MarketRegimeProvider infer ≤25 秒且不重試。Adapter 只回 schema-valid result 或 typed error；不得生成 fallback probabilities。Core 建立版本化 deterministic fallback `AnalysisResult`。
- Reasoning Context canonical JSON≤524288 bytes、provider tokenizer≤64000 tokens、Evidence≤120、Analysis≤32、Contradiction≤64、Limitations≤50、question/excerpt≤2000 scalars；deterministic truncate 並記 omissions。
- Reasoning sequence：primary → 最多一次 primary repair（≤60 秒）→ fallback → validate；invalid fallback 不發布。

## Artifact 與 observability

- Normal bundle 包含全部格式；degraded minimum 是 Final Report JSON、Evidence List JSON、Execution Log JSONL、Manifest JSON。缺任一 minimum 不可 publish。
- Manifest 最後產生，列 available/missing/reason 且不自列；Markdown/HTML/CSV renderer failure 可形成 `partial`。
- Major step event 包含 timestamp、task/execution、step、adapter、status、duration、retry count、safe parameters/summary、error code、remaining deadline。
- Log 不含 secret、token、完整 Authorization header、未遮罩 PII、完整 prompt/raw sensitive content；identity 使用 versioned-key HMAC pseudonym。

## 測試與安全

- Unit：value object、planner、formula、state、trust、fingerprint。
- Shared Contract：十個 Port 的 37 個 stable method IDs；fake 與 production adapters 使用相同 assertions。逐 operation 驗證完整 `x-method-policy`、method-specific `ErrorResult` 與 unknown exception → `unexpected_provider_error`；shared semantic assertions 覆蓋 Evidence offset/token TTL、Reasoning citation graph 與 Artifact Manifest 跨欄 invariant。
- Integration core：Core-owned tests 只在 `tests/integration/core/`；使用 fake/local boundaries，不連真實 provider/AWS。
- Integration provider：只在 `tests/integration/providers/`，顯式 opt-in；一般 CI 無 AWS/network/secret。
- Architecture：Domain/Application import 與 ownership path。
- E2E：fake adapters 覆蓋三題型、quota、preflight、degradation、deadline、citation、minimum bundle。
- 外部內容視為不可信；Collector 必須 SSRF/allowlist/DNS/IP/redirect/payload/robots/rate guard。Model 無 orchestration、network、database、secret tools。

## 變更 authority

Shared contract/schema/assertions 只由 Core Maintainer 核准。Provider 遇契約缺口，建立 `docs/proposals/PORT_CHANGE_<name>.md`（Problem、Proposed change、Compatibility impact、Testing requirements、SemVer impact），不得先改 DTO 或用 SDK type／untyped extension 穿透。Provider task 唯一權威是 `.kiro/specs/provider-adapters/tasks.md`。

Blocking/nonblocking 決策只在 `docs/architecture/open-questions.md` 維護。
