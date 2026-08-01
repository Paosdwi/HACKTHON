# Core Platform Implementation Tasks

- **版本**：0.2.0
- **狀態**：規格與共用契約已核准；所有產品實作工作均未開始
- **規則**：本文件只追蹤 Core-owned 工作。本輪不授權寫產品程式，不執行 Phase 1+。T01/T10 開始前，只要求 OQ-B001～OQ-B011 中與 Port schema freeze、Clean Architecture boundary 或 ownership 直接相關的決策及必要 ADR 由 owner 核准；OQ-B012～OQ-B015 採 phase-specific production gates，不阻塞 T10、Domain/interface、in-memory fake 或 architecture-test 工作。
- **Provider authority**：Provider adapter 的 checkbox、acceptance、驗證與完成狀態只在 `.kiro/specs/provider-adapters/tasks.md` 維護。

## 使用方式

每項工作固定列出：依賴、需求、允許修改目錄、驗收條件、測試種類。未列入「允許修改目錄」的路徑不得修改；若切片需要擴大範圍，先更新本文件並審核。完成時應在項目下記錄測試命令/結果與重要 artifact，不能只因程式可啟動就勾選。

## Phase 0 — 規格與 ADR Gate

- [x] **T00 — 核准核心規格並建立 dependency-specific decision gates**
  - **依賴**：無
  - **需求**：全部；特別是 CP-FR001、CP-FR006、CP-FR008、CP-FR011、CP-PORT-02
  - **允許修改目錄**：`.kiro/steering/`、`.kiro/specs/core-platform/`、`docs/architecture/`、`docs/specs/`
  - **驗收條件**：列名 owner 人工核准 OQ-B001～OQ-B011 中開始 T01/T10 所直接需要的決策與必要 ADR；OQ-B012～OQ-B015 保持 blocking，但只記錄並執行各自 phase-specific production gate，不得阻塞 T10、Domain entities、Application Port interface、in-memory fake 或 architecture tests；所有 ADR Status 明確；不可可靠判讀內容標 `SOURCE_TEXT_UNCERTAIN`；不得以文件/schema validation 自動視為核准。
  - **測試種類**：文件 review、traceability audit、schema example validation
  - **核准證據**：2026-08-01 Maintainer 明確核准 ADR-001～ADR-010 與 OQ-B001～OQ-B011；OQ-B012～OQ-B015 保留 phase-specific production gates。

- [x] **T01 — 固化 v1 Port schema 與 contract-test matrix**
  - **依賴**：T00
  - **需求**：CP-PORT-01..06、CP-ARCH-06
  - **允許修改目錄**：`docs/architecture/`、`docs/specs/`、`.kiro/specs/core-platform/`
  - **驗收條件**：common + 十個 Port Draft 2020-12 schemas 全部通過 meta-schema與離線 `$ref`；37/37 valid examples accepted、37/37 invalid examples rejected、37 stable CT IDs 一致；每個 operation 的 `x-method-policy` 完整覆蓋 timeout/retry/idempotency/concurrency，每個 method 有專屬 `ErrorResult` 且 unknown exception 映射 `unexpected_provider_error`；shared semantic assertions 覆蓋 Evidence offset/token TTL、Reasoning citation graph、Artifact Manifest 跨欄 invariant；Core Maintainer 完成人工 schema review 並核准 OQ-B004。移除 `lock_for_execution`/`CT-TASK-LOCK-EXECUTION-01` 是避免禁止的分步交易之 breaking draft change，未因驗證通過而 Approved；協作者可不讀 Core 內部程式實作 adapter。
  - **測試種類**：契約 review、JSON schema/meta-schema validation
  - **核准證據**：2026-08-01 Maintainer 人工核准 contract set 1.0.0；Kiro 驗證紀錄為 11 schemas、10 Ports、37/37 valid、37/37 invalid rejected、0 failures。

## Phase 1 — Architecture Guardrails 與核心骨架

- [x] **T10 — 建立 Clean Architecture package boundary 與 architecture tests**
  - **依賴**：T01；OQ-B001～OQ-B011 中與 T01 schema freeze／T10 ownership boundary 直接相關的決策
  - **Phase-specific gate**：不依賴 OQ-B012、OQ-B013、OQ-B014 或 OQ-B015。
  - **需求**：CP-ARCH-01..04、CP-PORT-03
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`src/crypto_trust_agent/infrastructure/`、`src/crypto_trust_agent/presentation/`、`tests/architecture/`
  - **驗收條件**：四層可匯入；Domain 無 FastAPI/Boto3/Bedrock/SageMaker/AgentCore/vendor SDK；Application 無 Infrastructure/Presentation/vendor imports；composition root 之外沒有 concrete adapter wiring。
  - **測試種類**：Architecture、import smoke test
  - **完成證據**：2026-08-01 `python -B -m unittest discover -s tests/architecture -p "test_*.py" -v`，4 tests passed；四層為 explicit packages，AST dependency guard 通過，未建立或接線任何 concrete Provider adapter。

- [x] **T11 — 建立 boundary schema/version/error primitives**
  - **依賴**：T10
  - **需求**：CP-PORT-02、CP-PORT-06、CP-ARCH-05..08
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/dto/`、`src/crypto_trust_agent/application/ports/`、`tests/unit/`、`tests/contract/`
  - **驗收條件**：UTC timestamp、canonical Decimal string、SemVer schema、distributed `DeadlineDTO`、receiver-local monotonic enforcement、typed error envelope 可驗證；wire 不含 runtime monotonic value；unknown provider exception 安全映射且不洩漏 raw exception/secret。
  - **測試種類**：Unit、schema、property-based、Contract
  - **完成證據**：2026-08-01 `python -B -m unittest discover -s tests/unit -p "test_*.py" -v`（6 passed）、`python -B -m unittest discover -s tests/contract -p "test_*.py" -v`（7 passed）、architecture regression（4 passed）；涵蓋 frozen `1.0.0`、UTC `Z`、ADR-004 grammar/ranges、六欄 DeadlineDTO、receiver-local monotonic deadline、expired-before-I/O 與 redacted `unexpected_provider_error`。

## Phase 2 — Domain Entities 與狀態機

- [x] **T20 — 實作 Task 與 Execution aggregates/state machines**
  - **依賴**：T11
  - **需求**：CP-FR001-01..10、CP-FR011-06..12
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`tests/unit/`
  - **驗收條件**：Task 與 Execution 只允許 design 定義的 transition；Pre-flight 前無 Formal Execution；terminal state 不倒退；版本衝突可偵測；admin rerun invariant 可由 domain policy 表達。
  - **測試種類**：Unit、state-transition table、property-based
  - **完成證據**：2026-08-01 state-machine targeted tests 10 passed；完整 unit regression 16 passed、common contract 7 passed、architecture 4 passed。Task pre-flight/lock/terminal 與 Execution dual pipeline path、terminal outcome、optimistic version、allowlisted admin second attempt 均由 immutable aggregate invariant 強制。

- [x] **T21 — 實作 Evidence、EvidenceClaimLink、EvidenceAssessment、AnalysisResult**
  - **依賴**：T11
  - **需求**：CP-FR005-*、CP-FR007-02
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`tests/unit/`
  - **驗收條件**：Evidence 建立後不可變且只用 canonical `raw_locator`；stance 只允許 supports/contradicts/context；assessment 以 sequence append-only 且 latest 可重現；AnalysisResult 有 input refs、quality、producer/ruleset version；跨 Task/quarantined/missing lineage 被拒絕。
  - **測試種類**：Unit、schema、immutability、property-based
  - **完成證據**：2026-08-01 Evidence targeted tests 7 passed；完整 unit regression 23 passed、common contract 7 passed、architecture 4 passed。Frozen dataclass 與 immutable mappings 保護 Evidence/Analysis；ContentReference half-open offset、HTTPS/dataset URL、hash/lineage、active-only linking、append-only sequence 與 max-sequence latest selection 均通過。

## Phase 3 — Deterministic Planning、Fingerprint 與 Repository Fakes

- [x] **T30 — 實作版本化 request fingerprint**
  - **依賴**：T00、T20
  - **需求**：CP-FR001-04..06、CP-ARCH-06
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`tests/unit/`
  - **驗收條件**：完全依核准 ADR canonicalize question/assets/timeframe；相等輸入產生相同 fingerprint；規則版本被持久化；Unicode/whitespace/order/timezone/boundary fixtures 有明確結果；不使用 wall-clock 隱含值。
  - **測試種類**：Unit、golden vectors、property-based
  - **完成證據**：2026-08-01 fingerprint targeted tests 5 passed；完整 unit regression 28 passed、architecture 4 passed。`fingerprint-1.0.0` 實作 NFKC/Unicode whitespace、allowlisted assets requested/canonical order、RFC3339 minute-aligned UTC、受限 RFC8785-compatible canonical JSON 與 lowercase SHA-256；函式不讀 wall clock。

- [x] **T31 — 實作 Deterministic Planner 與 source_requirement_matrix**
  - **依賴**：T20、T30
  - **需求**：CP-FR002-*、CP-FR003-01..02、CP-FR003-05
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`tests/unit/`
  - **驗收條件**：三題型產生固定 dimensions/categories/queries/budgets/ranges/plans；matrix 與主規格一致；官方 dataset 永為 required；相同 input/ruleset/clock snapshot 的 canonical output byte-equivalent；沒有 LLM/provider import/call；保留雙資產 requested order。
  - **測試種類**：Unit、golden snapshot、property-based、Architecture
  - **完成證據**：2026-08-01 planner targeted tests 4 passed；完整 unit regression 32 passed、architecture 4 passed。三題型 matrix、六類固定 jobs、官方 dataset market job、版本化 budgets/ranges/analysis steps、byte-equivalent canonical plan 與雙資產 first-priority requested order 均通過；無 LLM/provider dependency。

- [x] **T32 — 實作 Repository/Event/Clock Ports 與可控 fake adapters**
  - **依賴**：T01、T20、T21
  - **Phase-specific gate**：Application Port interfaces 與 in-memory fakes 不依賴 OQ-B012；fake transaction/quota 行為必須標示為 non-production，且不得宣稱 DynamoDB/distributed concurrency production-complete。
  - **需求**：CP-PORT-01..05、CP-ARCH-05
  - **允許修改目錄**：`src/crypto_trust_agent/application/ports/`、`src/crypto_trust_agent/application/dto/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/contract/`
  - **驗收條件**：TaskRepository、ExecutionRepository、EvidenceRepository、ArtifactRepository、EventPublisher、Clock 的 fake 實作完整；支援 atomic create-or-get、conditional transition、append-only assessment、artifact hash、recorded events 與 wall/monotonic fake time；`ExecutionRepository.acquire_quota_and_create` 是 revalidate + Task input lock + quota + Execution create + pass consume 的唯一原子 authority，TaskRepository fake 不得暴露獨立 execution lock；全部通過共享契約。
  - **測試種類**：Contract、concurrency、idempotency、fake smoke
  - **完成證據**：2026-08-01 `python -B -m unittest tests.contract.test_core_repository_fakes -v`（23 passed）；完整 contract regression（30 passed）、unit regression（32 passed）、architecture regression（4 passed），`python -B -m compileall -q src tests` 通過。六個 Application-owned Protocol、frozen DTO 與 non-production thread-safe fakes 覆蓋 26 個 frozen stable methods；40-way create-or-get、100-way atomic acquire、24h replay、tenant/fingerprint/preflight/quota binding、deadline、conditional transition、append-only snapshot assessment、artifact bytes/hash/manifest、recorded event dedup/redaction 與可控 wall/monotonic time均通過；TaskRepository 未暴露 execution lock。

## Phase 4 — Task API、Identity、Pre-flight 與 Formal Quota

- [x] **T40 — 實作 CreateTask use case 與 FastAPI boundary**
  - **依賴**：T30、T31、T32
  - **需求**：CP-FR001-*、CP-ARCH-04
  - **允許修改目錄**：`src/crypto_trust_agent/application/use_cases/`、`src/crypto_trust_agent/presentation/api/`、`src/crypto_trust_agent/infrastructure/identity/`、`tests/unit/`、`tests/integration/core/`
  - **驗收條件**：verified Cognito principal 是唯一 user identity；body/query/custom header 無法 override；每 user 10/hour 且 reuse 也計數；24h create-or-get 併發只得一 Task；合法新建/重用與 401/422/429 mapping 正確；log 僅有 pseudonym/sanitized fields。
  - **測試種類**：Unit、API Integration、security、concurrency、Contract
  - **完成證據**：2026-08-01 test-first red→green；`python -B -m unittest tests.unit.test_create_task -v`（9 passed）、`python -B -m unittest tests.integration.core.test_task_api -v`（9 passed，包含實際 FastAPI `TestClient` composition）、targeted Task/Execution contract（9 passed）；完整 unit regression（41 passed）、contract regression（30 passed）、architecture regression（4 passed），`python -B -m compileall -q src tests` 通過。verified Cognito-style principal（injectable local verifier，無 AWS/network adapter）為唯一 identity；issuer/audience/signature-bound verifier/expiry/sub/admin-group negative cases、body/query/custom-header override rejection、authenticated request count-first、reuse 計數、10/hour 429、10-way use-case/API concurrency 單一 24h Task、created/reused 201/200、401/422、三題型 plan、HMAC pseudonym redaction、audit failure isolation、64KiB ASGI body bound 與 FastAPI mount 均通過。Runtime/test dependencies 以 `pyproject.toml` 精確釘選。

- [x] **T41 — 實作 Pre-flight readiness 與獨立限流**
  - **依賴**：T32、T40
  - **需求**：CP-FR011-01..05
  - **允許修改目錄**：`src/crypto_trust_agent/application/use_cases/`、`src/crypto_trust_agent/application/ports/`、`src/crypto_trust_agent/presentation/api/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/integration/core/`
  - **驗收條件**：檢查 input/dataset/Nova/Opus/SageMaker/allowlist readiness；不執行完整 inference；第 4 次/minute 回 429 且 fake probes 呼叫數不增加；不建立 Execution、不消耗 quota；不健康原因可安全顯示。
  - **測試種類**：Unit、Contract、API Integration、rate-limit、security
  - **完成證據**：2026-08-01 test-first red→green；`python -B -m unittest tests.unit.test_preflight tests.integration.core.test_preflight_api -v`（13 passed），targeted frozen TaskRepository contract（23 passed）；完整 unit regression（49 passed）、contract regression（30 passed）、architecture regression（4 passed）、API regression（14 passed），`python -B -m compileall -q src tests` 與 `git diff --check` 通過。Pre-flight 在任何 local/provider probe 前原子取得每 Task 3/minute slot，第 4 次回 429 且 probe count 不增加；input/official dataset 使用 Application-owned local readiness Port，Nova/Opus/SageMaker/external allowlist 消費 frozen 3 秒 side-effect-free health shape，無 inference。成功 pass 從 probes 完成時起固定 60 秒，綁定 `task_id`/`task_version`/`input_lock_hash`/`dependency_snapshot_hash` 且保留未消耗 single-use fields；所有失敗、429 與不健康結果均不建立 Execution、不消耗 formal quota，只輸出 allowlisted safe code。ASGI/FastAPI route 僅採 verified principal，body/query/custom identity override、cross-tenant、deadline-before-I/O、unknown exception sanitization 與 event-loop offload 均通過；未修改 frozen schema/DTO contract、Provider directories 或 provider plan。

- [x] **T42 — 實作 Formal quota、Execution create 與 admin rerun**
  - **依賴**：T20、T32、T41
  - **Phase-specific gate**：Domain policy、Application orchestration 與 in-memory fake acceptance 不依賴 OQ-B012；production persistence、DynamoDB transaction/key/index 與正式 distributed concurrency/quota integration 仍由 OQ-B012 阻塞。
  - **需求**：CP-FR011-06..10
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/use_cases/`、`src/crypto_trust_agent/presentation/api/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/contract/`、`tests/integration/core/`
  - **驗收條件**：固定 60 秒 TTL 的 single-use pass 綁定 `task_id`/`task_version`/`input_lock_hash`/`dependency_snapshot_hash`；只透過 `ExecutionRepository.acquire_quota_and_create` 原子重驗 binding/freshness、鎖定 Task input、取得 quota、建立 Execution 與 consume pass，TaskRepository 無獨立 lock；failure/stale/expired/rollback 不耗 quota；scope 是 `(trusted user, fingerprint)` 且跨 Task 不重置；第二次只能 admin + allowlisted technical failure；第三次或重跑再次故障轉 manual case。
  - **測試種類**：Unit、Repository Contract、concurrency、API Integration、authorization
  - **完成證據**：2026-08-01 test-first contract-drift/rollback/replay red→green；T42 targeted unit+contract+API（17 passed）、Task/Execution repository concurrency/idempotency（9 passed）、完整 unit regression（55 passed）、完整 contract regression（52 passed）、完整 Core API integration（20 passed）、architecture regression（4 passed）。`acquire_quota_and_create` fake 是唯一 non-production atomic authority；100-way 競爭單一 winner，60 秒邊界、全部 pass bindings、receiver-local monotonic deadline、900 秒 absolute UTC deadline、跨 Task quota、stable same-operation replay、caller payload collision、verified-admin allowlist、第二次失敗 frozen `rerun_failed` manual case、第三次拒絕、unknown exception redaction及三階段 rollback 均通過。所有實際 success/error/manual-case payload 通過 frozen ExecutionRepository schema；未修改 frozen contract，OQ-B012 production persistence/distributed transaction slice仍未完成。

## Phase 5 — Evidence 與 Analysis Pipeline

- [x] **T50 — 實作 SourceCollector/EvidenceExtractor Ports、DTO 與 scenario fakes**
  - **依賴**：T01、T21、T32
  - **需求**：CP-FR003-*、CP-FR004-*、CP-PORT-01..05
  - **允許修改目錄**：`src/crypto_trust_agent/application/ports/`、`src/crypto_trust_agent/application/dto/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/contract/`
  - **驗收條件**：planned job 只接受核准 category/query/budget；outcome 僅 success/skipped/failed；RawRecord 含 locator/hash/time/provenance；Extractor 只回 schema-valid claims 或 typed invalid/quarantine outcome；fake 可模擬 timeout、invalid schema、repair 與 URL rejection。
  - **測試種類**：Contract、schema、timeout、security negative cases
  - **完成證據**：2026-08-01 test-first red→green；`python -B -m unittest tests.contract.test_source_collector_evidence_extractor_fakes -v`（17 passed）、完整 unit regression（55 passed）、完整 contract regression（47 passed）、architecture regression（4 passed），`python -B -m compileall -q src tests/contract` 與 `git diff --check` 通過。新增 frozen/slots exact-wire DTO、runtime-checkable `SourceCollector`/`EvidenceExtractor`、`non_production` deterministic scenario fakes，以及 `tests/contract/shared_collector_extractor_assertions.py` 共用 assertions；涵蓋六個 frozen stable methods/CT IDs、method-specific errors、recorded replay、deadline-before-I/O、完整 RawRecord lineage/provenance/security、HTTPS/443/SSRF/redirect/payload bounds、timeout/invalid schema/repair/quarantine、health/capabilities 與 unknown exception redaction。未修改 frozen schema、Provider-owned adapter 路徑或 Provider plan；未連線 AWS/network。

- [x] **T51 — 實作 Evidence normalization、link、assessment 與 isolation**
  - **依賴**：T21、T50
  - **需求**：CP-FR005-01..09
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`tests/unit/`、`tests/integration/core/`
  - **驗收條件**：程式產生/驗證 ID、time/hash/locator/version；Evidence immutable；links/assessments append；latest assessment selection 可重現且寫 log；lineage missing/cross-task/missing/quarantined refs 被隔離；shared semantic assertions 驗證 `ContentReferenceDTO.offset` half-open range 與 strong snapshot token TTL/expiry、tamper/cross-task/cross-filter rejection。
  - **測試種類**：Unit、Repository Contract、Dataset-to-Evidence Integration、citation isolation
  - **完成證據**：2026-08-01 test-first red→green；T51 targeted normalization/assessment/workflow/shared semantics（19 passed）、完整 unit regression（66 passed）、完整 contract regression（59 passed）、完整 Core integration（21 passed）、architecture regression（4 passed）。Evidence normalization 驗證 task/execution/raw/hash lineage、canonical HTTPS URL、Unicode scalar half-open quote offset及 supports/contradicts/context；assessment append 使用 injected ID/Clock、append-only sequence與 operation payload replay；latest selection 綁 strong snapshot 並記錄 assessment ID/version/ruleset。non-production fake-only explicit TTL 在 999ms 有效、1000ms 過期，token/cursor 拒絕 tamper、cross-task、cross-filter 與未簽發 cursor；既有 constructor 保持 non-expiring 相容，不宣稱 production TTL。未修改 frozen schema 或 Provider-owned adapter 路徑。

- [x] **T52 — 實作 dedup/independence 與 Trust/Confidence strategy interfaces及 deterministic fakes**
  - **依賴**：T51
  - **Phase-specific gate**：不依賴 OQ-B014 或 OQ-B015；本 task 只建立穩定 strategy boundary、版本欄位、deterministic fake behavior 與 contradiction flow，不宣稱 production ruleset。
  - **需求**：CP-FR006-*、CP-FR008-*
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/integration/core/`
  - **驗收條件**：Evidence／EvidenceClaimLink／EvidenceAssessment entities 與 strategy interfaces 可接受版本化 ruleset；fake dedup/grouping/scoring 明確標示 `non_production` 且可重現；六類 contradiction 保留雙邊 refs；counter evidence 不被過濾；不得以 fake threshold、allowlist 或 confidence formula 宣稱 production-complete。
  - **測試種類**：Unit、fake golden fixtures、property-based、Integration
  - **完成證據**：2026-08-01 test-first red→green；T52 targeted unit/integration（7 passed）、完整 unit regression（72 passed）、contract regression（59 passed）、Core integration regression（22 passed）、architecture regression（4 passed），public import smoke 通過。新增五個 Core-internal runtime-checkable strategy interfaces：duplicate detection、independence grouping、trust components、contradiction detection、confidence composition；所有 DTO 接受明示 ruleset version。non-production fakes 以 exact URL/hash、stable evidence-ID tie-break、group-level corroboration=1、五個可見 component 與透明 Decimal fixture arithmetic 提供可重現結果；六類 contradiction 全保留 distinct bilateral refs，counter-evidence 始終留在 retained IDs。未固化 production similarity threshold、allowlist、representative policy、trust/severity/confidence ruleset；OQ-B014/B015 仍分別阻塞 T54/T55。

- [x] **T53 — 實作 historical deterministic market analysis、官方 dataset reader 與 live-extension fake**
  - **依賴**：T21、T32、T00
  - **Phase-specific gate**：官方 CSV dataset reader、historical-range analysis、MarketRegimeProvider fake 與 fake live-extension adapter 不依賴 OQ-B013；真實 live market provider、credential/readiness 與 2026-05-31 後 production-complete reporting 仍由 OQ-B013 阻塞。
  - **需求**：CP-FR007-01..08
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/contract/`、`tests/integration/core/`
  - **驗收條件**：公式有版本、canonical Decimal string/UTC、warm-up/reporting range；官方 readiness failure 不替換；historical range 由官方 CSV 驗證；fake live point 保留 provenance/transition date 且缺口不 forward-fill，並明確標示非 production provider；跨資產 volume 不直接比較；MarketRegimeProvider fake 模擬 25 秒 timeout/zero retry，Core 建立版本化 deterministic fallback AnalysisResult。
  - **測試種類**：Unit、formula golden data、Dataset Integration、Port Contract、fake timeout
  - **完成證據**：2026-08-01 test-first red→green；T53 targeted unit/contract/full-dataset integration（14 passed）、完整 unit regression（78 passed）、contract regression（66 passed）、Core integration regression（23 passed）、architecture regression（4 passed）與 frozen import smoke 通過。metadata-relative reader 只開啟 `dataset_metadata.json` 宣告的安全相對 CSV，拒絕 `__MACOSX`/`._*`/`.DS_Store`，直接以 Decimal 解析並驗證 UTC/1d/USDT、columns、asset/pair、row count/period、ordered gap-free dates、numeric/nonnegative/OHLC；完整五資產各 1,826 rows（共 9,130）通過，lineage 標示 hackathon-provided official dataset 並保留 `public_market_data`。`market-formulas-1.0.0` 提供 return/high/low/volume change/volatility/drawdown/SMA/trend/regime 與明示 warm-up/reporting range；fake live extension 保留逐點 provenance/transition_date且不 forward-fill。Frozen MarketRegimeProvider `infer`/`health_check` DTO/Protocol/fake/shared assertions 驗證 CT IDs、25s/3s、Core retry owner、max attempts 1、zero hidden retry、probability sum、typed/unknown/deadline errors，且 schema-valid opaque model version 可保留至 AnalysisResult；provider 不產生 fallback，Core timeout path建立版本化 deterministic fallback AnalysisResult。未修改原始 CSV、frozen schemas或Provider-owned adapter路徑；OQ-B013仍阻塞真實 live provider/credentials及2026-05-31後 production-complete reporting。

- [ ] **T54 — 實作 production dedup 與 independence ruleset**
  - **依賴**：T52；OQ-B014 由列名 owner 核准
  - **Phase-specific gate**：OQ-B014 只阻塞本 production task，不回溯阻塞 T10、Evidence entities、strategy interface 或 fake dedup。
  - **需求**：CP-FR006-*
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`tests/unit/`、`tests/integration/core/`
  - **驗收條件**：production ruleset 固化 press/domain allowlist、similarity thresholds、event clustering、representative tie-break 與 independence scoring；版本化、可重算，且同一 independence group 不增加 corroboration。
  - **測試種類**：Unit、golden clusters、property-based、Integration

- [ ] **T55 — 實作 production trust、contradiction severity 與 final confidence rulesets**
  - **依賴**：T52、T54；OQ-B015 由列名 owner 核准
  - **Phase-specific gate**：OQ-B015 只阻塞本 production task，不回溯阻塞 T10、EvidenceAssessment schema/entity、strategy interface、fake deterministic scoring、report DTO 或 renderer skeleton。
  - **需求**：CP-FR008-*
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`tests/unit/`、`tests/integration/core/`
  - **驗收條件**：production source trust、relevance、freshness、consistency、contradiction severity 與 final confidence rulesets 均版本化、可拆解、可重算；不得以 opaque LLM score 取代。
  - **測試種類**：Unit、golden score vectors、property-based、Integration

## Phase 6 — Formal Orchestration、Reasoning 與 Publication

- [x] **T60 — 實作 900 秒 DAG Orchestrator 與 deadline policy**
  - **依賴**：T42、T50、T51、T52、T53
  - **需求**：CP-FR003-02..05、CP-FR011-11..12、CP-ARCH-08
  - **允許修改目錄**：`src/crypto_trust_agent/application/orchestration/`、`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/integration/core/`
  - **驗收條件**：Core runtime 使用 local monotonic 900 秒 deadline；跨 process 只傳 UTC deadline/budget/sent time/safety margin，receiver 重建 local monotonic deadline；Step Functions/Lambda/HTTP/SDK outer guards 生效；stage hard deadlines/buffer、pipeline overlap、雙資產平行、低優先停止與所有 planned jobs 收斂；無無界 retry/sleep。
  - **測試種類**：Unit with fake Clock、DAG Integration、deadline/timeout、property-based
  - **完成證據**：2026-08-01 test-first red→green；`python -B -m unittest tests.unit.test_formal_run_orchestrator tests.integration.core.test_formal_run_orchestration -v`（26 passed）、完整 unit regression（121 passed）、Core integration regression（25 passed）、architecture regression（4 passed）。版本化 `formal-run-budget-1.0.0` 固定 900,000ms hard deadline 與 11-stage deterministic DAG；wire 僅使用 frozen 六欄 `DeadlineDTO`，receiver 以 injected Clock 重建 local monotonic deadline。required/optional degradation、provider unavailable、timeout、cancellation、quarantine/no verified evidence、contradiction retention、replay/payload conflict、zero retry、低優先停止與 terminal convergence 均通過；T62 僅保留穩定 publication placeholder 且未宣稱完成，未連線 AWS/network 或呼叫真實模型。

- [x] **T61 — 實作 Structured Reasoning Context 與 ReasoningProvider fake**
  - **依賴**：T52、T53、T60
  - **需求**：CP-FR009-*、CP-FR005-06..07
  - **允許修改目錄**：`src/crypto_trust_agent/domain/`、`src/crypto_trust_agent/application/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/contract/`
  - **驗收條件**：context task-scoped、bounded、不含 raw HTML/secret；shared semantic assertions 驗證 Fact→Evidence/Analysis、Inference→Fact、Conclusion→Fact/Inference graph，missing/cross-task/quarantined citation 拒絕；Opus 一次 repair 共用 60 秒，之後 Sonnet；fallback invalid 不發布。
  - **測試種類**：Unit、schema/citation、Port Contract、timeout/fallback
  - **完成證據**：2026-08-01 test-first red→green；`python -B -m unittest tests.unit.test_reasoning_context tests.unit.test_reasoning_sequence tests.contract.test_reasoning_fake -v`（28 passed）、完整 unit regression（119 passed）、contract regression（75 passed）、architecture regression（4 passed）。新增 frozen `ReasoningProvider` 的 `generate`／`repair`／`health_check` exact-wire DTO/Protocol、Core-owned non-production fake 與 `shared_reasoning_assertions`；三個 stable CT IDs、60s/60s/3s timeout、Core retry owner、max attempts 1、zero hidden retry、typed/unknown error、receiver-local deadline、replay/payload conflict均通過。Structured Context 以 canonical JSON/hash deterministic projection，限制 524288 bytes/64000 tokens/Evidence 120/Analysis 32/Contradiction 64/Limitations 50，記錄 deterministic omissions，只接受 task-scoped active Evidence與 max-sequence assessment；missing/cross-task/quarantined/hallucinated citation拒絕，counter evidence、雙邊 contradiction與 degraded market limitation保留。Core sequence 固定 primary→最多一次 primary repair（共用 primary 60秒 window）→fallback；invalid fallback 明確 `publishable=False`，未開始 T62 或進行 artifact publication。未修改 frozen schema、Provider-owned adapter路徑或原始 CSV，未連線 AWS/network/真實模型。

- [x] **T62 — 實作 canonical report、Evidence List、Execution Log、renderers 與 Manifest**
  - **依賴**：T32、T61
  - **需求**：CP-FR010-*、CP-FR011-13
  - **允許修改目錄**：`src/crypto_trust_agent/application/`、`src/crypto_trust_agent/infrastructure/artifacts/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/contract/`、`tests/integration/core/`
  - **驗收條件**：publication 前 schema/citation/numeric/lineage audit；normal run 全格式；degraded minimum 固定 Final Report JSON + Evidence List JSON + Execution Log JSONL + Manifest JSON，缺任一不 publish；Manifest 最後寫，shared semantic assertions 驗證 available/missing/reason、artifact descriptors/hash 與 self-exclusion 跨欄 invariant；renderer failure 可 partial；assessment 使用可追溯。
  - **測試種類**：Unit、golden rendering、Artifact Contract、Integration、security/redaction
  - **完成證據**：2026-08-01 test-first red→green；T62 targeted unit/contract/Core integration（18 passed）、完整 unit regression（129 passed）、完整 contract regression（84 passed）、完整 Core integration（26 passed）、architecture regression（4 passed），`python -m compileall -q src tests/unit/test_artifact_publication.py tests/contract/test_artifact_publication_fake.py tests/integration/core/test_artifact_publication.py` 與 `git diff --check` 通過。依 Master Spec 固定 live-extension `transition_date=2026-05-31`，錯誤 `2026-06-01` fail-closed。`EvidenceListEntryDTO` 直接建構會驗證 schema/ID、bounded source/type、HTTPS locator、UTC/content、至少一筆合法 related claim、canonical SHA-256、完整 lineage 與 assessment identity/version/sequence；`EvidenceListDTO.from_records` 拒絕無 claim link Evidence。`ArtifactPublicationService` 明示注入 Application-owned `Clock`，以 receiver-local monotonic deadline 對每次 `put`/`put_manifest` 取 repository 5,000ms、formal absolute deadline remaining 與 final-write 25,000ms hard boundary remaining 的最小值；daemon outer guard 維持 max attempts 1/zero hidden retry，blocking put/manifest、Clock/unknown exception、late-result isolation/no-retry、required failure/timeout 不發布 Manifest均有測試，未知錯誤安全映射 `unexpected_provider_error`，Manifest 仍最後呼叫。Application-owned canonical Final Report/Evidence List/Execution Log models 在任何 artifact write 前執行 output schema、citation graph、Evidence/Analysis traceability、ADR-004 Decimal、Evidence lineage 與 assessment ID/version 一致性驗證；JSONL 移除 prompt/JWT/token/secret/raw content。Markdown/HTML/CSV 只由 canonical models render；renderer failure 以安全 reason 在 report/log/manifest 揭露並只發布四檔 minimum bundle。Core local `FakeArtifactRepository` 驗證 decoded bytes SHA-256/size、same logical key+same hash no-op、different hash conflict、required artifact failure 不發布 Manifest、available/missing/descriptors 跨欄一致、Manifest 最後寫且 self-excluded；normal bundle 7 檔、degraded bundle 4 檔。未修改 frozen schema/DTO/Port、Provider-owned adapter 路徑，未連線 AWS/network，未開始 T63。
  - **PA74 checkpoint**：Provider ArtifactRepository adapter 必須直接實作 frozen `ArtifactRepository` 1.0.0 五方法與 method-specific typed errors，保持 decoded-byte hash/size、conditional logical-key idempotency/conflict、minimum artifacts stored-before-manifest、Manifest-last/self-exclusion/available-missing 跨欄 invariant及零 hidden retry；以同一 T62 contract assertions 驗證 production storage，Core 不要求 schema/DTO extension，且本完成狀態不代表 PA74 已完成。

- [x] **T64 — 實作 Formal Run → Canonical Artifact Publication Bridge**
  - **依賴**：T60、T61、T62
  - **需求**：CP-FR010-*、CP-FR011-11..13、CP-ARCH-07
  - **允許修改目錄**：`src/crypto_trust_agent/application/orchestration/`、`src/crypto_trust_agent/application/publication.py`、`src/crypto_trust_agent/application/dto/`、`src/crypto_trust_agent/infrastructure/fakes/`、`tests/unit/`、`tests/contract/`、`tests/integration/core/`、`.kiro/specs/core-platform/tasks.md`
  - **驗收條件**：ARTIFACT_PLACEHOLDER stage 真正呼叫 ArtifactPublicationService；Application-owned FormalRunArtifactAssembler 從該次 Evidence/Assessment/Analysis/Reasoning 建立 FinalReportDTO、EvidenceListDTO、ExecutionLogDTO、ArtifactPublicationRequest；publication outcome 寫回 FormalRunResult；terminal state 反映 complete/partial/failed publication；Manifest 最後寫；publication budget 25 秒、hard deadline 900 秒；renderer failure 可 partial；fake pipeline 每 stage 回傳有意義 canonical outputs，不使用固定 bundle 或 MagicMock。
  - **測試種類**：Unit、Artifact Contract、Core Integration、deadline/timeout、regression
  - **完成證據**：2026-08-01 最終人工驗收重跑（分支 `feature/core-platform`）；`python -B -m unittest tests.integration.core.test_publication_bridge -v`（18 passed，0 failure、0 error、0 skipped），Formal Run unit regression（24 passed），Artifact publication unit/contract/Core integration regression（18 passed），完整 unit regression（133 passed）、contract regression（97 passed）、architecture regression（4 passed）、Core integration regression（64 passed），`python -B -m compileall -q src tests` 與 `git diff --check` 均通過。七個 payload contribution DTO 與外層 `PipelineContributionDTO` 的 `schema_version` 均嚴格要求 `SCHEMA_VERSION=1.0.0`；unknown payload/outer version 建構時拒絕，orchestrator 累積前亦 defense-in-depth 驗證，未知版本會以 `stage_contribution_invalid` fail-closed，publication 與 artifact repository write 均為零次；canonical 1.0.0 `to_wire` shape 維持不變。新增 Application-owned frozen/bounded/serializable discriminated stage contribution DTO；`FormalRunStepResult` 明確回傳 typed contributions，executor 僅接收 immutable bounded `PipelineSnapshot`，不傳 mutable context。Orchestrator 透過 injected `pipeline_context_factory` 為每次 fresh execution 建立獨立 context，依 stage ownership、task/execution identity、bounds、duplicate ID、lineage 與 citation fail-closed 驗證後累積；COLLECTION 僅貢獻 raw-record，EXTRACTION 貢獻唯一 Evidence/ClaimLink，ASSESSMENT、MARKET_ANALYSIS、STRATEGY_EVALUATION、REASONING_BOUNDARY 各自貢獻其 canonical output，fake 不再暴露 `bind_pipeline_context`／`pipeline_context`。ARTIFACT_PLACEHOLDER 以 injected Clock 計算 `min(25,000ms, formal absolute remaining, local monotonic remaining, artifact-stage remaining)`，`deadline_at_utc=now+effective_budget` 且晚於 `sent_at_utc`；無 safety-margin budget 時不呼叫 assembler/publication。`FormalRunPublicationSummaryDTO` 取代 untyped dict/object tuple；assembler failure 與 publish failure分別記錄 `assembler_invalid`、`publication_deadline_exceeded`、`artifact_repository_failure`、`publication_integrity_failure` 或 `unexpected_publication_error`，實際呼叫 publish 前才標記 attempted。測試證明 publish exactly once、Manifest write last、不同 question 產生不同 Final Report hash、Evidence/assessment/analysis/reasoning 可追溯、assessment ID/version 進 Execution Log、duplicate Evidence fail-closed，以及輸出不含敏感 payload。未連線 AWS/network、未 commit/push；T63 保持未完成。

- [ ] **T63 — 實作 Demo UI 與 artifact/evidence rendering**
  - **依賴**：T40、T41、T42、T62、T64
  - **需求**：CP-FR011-13、CP-FR010-01..07
  - **允許修改目錄**：`src/crypto_trust_agent/application/use_cases/demo_ui.py`、`src/crypto_trust_agent/presentation/api/demo_ui_api.py`、`src/crypto_trust_agent/presentation/demo_ui/`、`tests/integration/core/`、`tests/e2e/`、`.kiro/specs/core-platform/tasks.md`
  - **目錄擴充原因**：Demo UI 需要 Application-owned orchestration/query/download boundary（組合既有 CreateTask、Preflight、StartFormalExecution use cases 與 ArtifactRepository Port），Presentation 不得直接存取 Repository；Core Owner 於第二次審核時核准。
  - **驗收條件**：顯示 progress、remaining time、partial reasons、artifact status；可檢視 Evidence lineage/assessment/contradictions 並下載 artifacts；UI 不直連 repository/S3、不接收 user override；不健康 pre-flight 顯示安全原因。
  - **測試種類**：UI component/integration、accessibility smoke、E2E

## Phase 7 — Provider cross-plan references（不追蹤狀態）

> 本節不是執行清單，不含 checkbox、acceptance 或 completion authority。唯一狀態來源是 `.kiro/specs/provider-adapters/tasks.md`；Core Kiro 不得執行、勾選或宣告完成以下 Provider 工作。

### T70 — Collector/Web Grounding adapter reference

- **OWNER: PROVIDER_COLLABORATOR**
- **Authority**：provider-adapters `PA70`
- **Core dependency**：T50 + 已核准 ADR-007/OQ-B008
- **Phase-specific gate**：OQ-B013 僅適用於真實 live-extension provider slice；一般 Collector/Web Grounding adapter 不因此受阻塞。
- **Rule**：此 reference 只供 T81 dependency graph 使用；進度、允許路徑、acceptance 與 validation 只看 Provider plan。

### T71 — EvidenceExtractor adapter reference

- **OWNER: PROVIDER_COLLABORATOR**
- **Authority**：provider-adapters `PA71`
- **Core dependency**：T50
- **Rule**：同上。

### T72 — MarketRegimeProvider adapter reference

- **OWNER: PROVIDER_COLLABORATOR**
- **Authority**：provider-adapters `PA72`
- **Core dependency**：T53
- **Rule**：Adapter 不得生成 fallback probabilities；Core T53 擁有 deterministic fallback AnalysisResult。

### T73 — ReasoningProvider adapter reference

- **OWNER: PROVIDER_COLLABORATOR**
- **Authority**：provider-adapters `PA73`
- **Core dependency**：T61
- **Rule**：同上；repair/fallback branch authority 留在 Core。

### T74 — Persistence/event/artifact/observability adapter reference

- **OWNER: PROVIDER_COLLABORATOR**
- **Authority**：provider-adapters `PA74`
- **Core dependency**：T32 + T62
- **Phase-specific gate**：OQ-B012 必須在 production persistence adapter、DynamoDB transaction/key/index 或正式 distributed concurrency/quota integration slice 開始前核准；event、artifact、observability 與 in-memory fake slices 不因此受阻塞。
- **Rule**：同上。

## Phase 8 — 最終 E2E 與 Dress Rehearsal

- [ ] **T80 — Fake-adapter 本機完整 E2E**
  - **依賴**：T60、T61、T62、T63
  - **需求**：CP-PORT-05 與全部 FR 驗收
  - **允許修改目錄**：`tests/e2e/`、`tests/fixtures/`、`src/crypto_trust_agent/infrastructure/fakes/`；若發現產品缺陷，只可回到缺陷所屬 task 的允許目錄並先更新 task 記錄
  - **驗收條件**：三題型成功；source matrix；Task 24h reuse；10/hour；Pre-flight 3/minute；跨 task quota；admin rerun；optional/required partial；Nova quarantine；SageMaker timeout；Opus repair/Sonnet invalid；deadline；雙資產 first-priority；citation audit；完整 artifact bundle。
  - **測試種類**：E2E、fault injection、deterministic replay、security regression

- [ ] **T81 — AWS integration E2E 與 Phase F 900 秒 Dress Rehearsal**
  - **依賴**：Core T80、T54、T55；Provider authority 的 PA70、PA71、PA72、PA73、PA74 均完成（Core T70–T74 僅為 references）；production persistence/live-extension slices 分別取得 OQ-B012/OQ-B013 核准
  - **需求**：CP-FR011-11..13、CP-FR010-*、Master Spec 可可靠確認的完成定義
  - **允許修改目錄**：`tests/e2e/`、`.kiro/specs/core-platform/` 與已核准 Core-owned deployment evidence 路徑；Provider integration/adapter 修改仍只能依 provider-adapters tasks authority
  - **驗收條件**：接近正式 Evidence 規模下全流程 <900 秒；Final Report JSON/MD/HTML、Evidence JSON/CSV、JSONL log、hash、S3 writes、Manifest 的 final-write 實測符合或校準 20 秒 Target；任何 budget 調整先更新 Spec 且不放寬 900 秒；API/UI/degradation/citation audit 全通過。
  - **測試種類**：Integration opt-in、E2E、load/latency、chaos/fault、security/audit

- [ ] **T82 — 最終規格/程式一致性與交付審核**
  - **依賴**：T81
  - **需求**：全部
  - **允許修改目錄**：`.kiro/`、`docs/`、測試結果指出且已核准的精確產品目錄
  - **驗收條件**：requirements/design/tasks/Ports 與實際狀態一致；所有 Core checkbox 有驗證證據，Provider completion 只引用 provider-adapters authority；Open Questions 已人工決議或明列風險；runbook/deployment/Demo evidence 更新；無 secret/敏感 fixture；完成定義逐條簽核。
  - **測試種類**：全套 regression、traceability audit、dependency/license/security review

## 暫停條件

遇到以下任一情況應停止實作並回到 Spec review：

- 需要改動已核准 Domain contract 或 Port major schema。
- 協作者 adapter 要求 SDK type 進入 Application/Domain。
- 無法在 stage/900 秒 deadline 內滿足 timeout/retry。
- 發現 `user_id` 可能由未驗證 client input 進入核心。
- 需要在 log 寫入 prompt、secret、token 或完整敏感 raw content 才能除錯。
- 原始規格文字不可可靠判讀、彼此矛盾，或 Open Question 會改變驗收結果。
