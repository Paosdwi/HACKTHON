# Provider Adapters Tasks

- **版本**：0.1.0
- **狀態**：Provider 規格基線已核准；PA00 等待協作者確認，所有實作工作未開始
- **唯一 authority**：本文件是 Provider adapter checkbox、acceptance、validation 與 completion 的唯一狀態來源。
- **禁止**：Core Kiro 不得執行或勾選本文件工作；Core T70–T74 只是 cross-plan references。

## Gate

- [x] **PA00 — Provider Spec / blocking decisions / shared schemas 核准**
  - **OWNER: PROVIDER_COLLABORATOR + CORE_MAINTAINER**
  - **依賴**：Core T01；OQ-B004/B008/B011 與開始所選 adapter slice 直接需要的 capability decision 人工核准；OQ-B012～OQ-B015 不作為所有 Provider 工作的全域 gate
  - **允許修改**：`.kiro/specs/provider-adapters/`、Provider 可提交 `docs/proposals/PORT_CHANGE_<name>.md`
  - **驗收**：Provider 確認十個 Port、37 個 stable methods、37 valid/37 invalid examples、逐 operation `x-method-policy`、method-specific `ErrorResult`（含 unknown exception → `unexpected_provider_error`）、ownership、test harness boundary、deadline/Decimal/Evidence/fallback semantics與 shared semantic assertions；Core Maintainer 完成人工 review；OQ-B012～OQ-B015 的 phase-specific production gates 已標記；ADR status 明確更新，不能只以 schema validation 代替批准。
  - **測試**：Spec review、schema/traceability review

### Phase-specific production gates

- **OQ-B012**：只阻塞 PA74 的 production persistence／DynamoDB transaction-key-index／distributed concurrency-quota integration slice；不阻塞 event、artifact、observability、interface 或 fake 工作。
- **OQ-B013**：只阻塞 PA70 中的真實 live-extension provider、credentials/readiness 與 2026-05-31 後 production-complete reporting slice；不阻塞一般 Collector/Web Grounding，也不阻塞 PA72 MarketRegime inference adapter。
- **OQ-B014**：只阻塞 production dedup/independence ruleset 的 provider integration evidence；不阻塞 Collector、EvidenceExtractor、MarketRegimeProvider 或 ReasoningProvider 的無關 adapter 工作。
- **OQ-B015**：只阻塞 production trust/confidence/contradiction-severity ruleset 的 provider integration evidence；不阻塞 Collector、EvidenceExtractor、MarketRegimeProvider、ReasoningProvider、report DTO 或 renderer skeleton 的無關工作。
- 所有 fake/fixture behavior 必須標示 non-production；未核准的 production adapter/ruleset 不得接入 production composition root。上述 gate 不勾選或完成任何 task。

## Adapter work

- [x] **PA70 — Collector / Web Grounding adapters**
  - **OWNER: PROVIDER_COLLABORATOR**
  - **Core reference/dependency**：T70 reference；Core T50；已核准 collector security decision
  - **Phase-specific gate**：一般 Collector/Web Grounding 工作不依賴 OQ-B013；只有真實 live-extension provider、credential/readiness 與 2026-05-31 後 production-complete reporting slice 必須先核准 OQ-B013。
  - **需求**：PA-REQ-*、PA-COL-*
  - **允許修改**：`src/crypto_trust_agent/infrastructure/collectors/`、自身 capability 的 `src/crypto_trust_agent/infrastructure/aws/` module、`tests/contract/` provider harness/fixtures/evidence、`tests/integration/providers/`
  - **驗收**：核准 category/query/range；static/Playwright selection；allowlist/DNS/IP/SSRF/robots/rate/payload/redirect/timeout；raw/clean lineage；每 job terminal；Web discovery 不改 plan；safe events。
  - **測試**：Shared Contract、schema negative、SSRF/security、record/replay、opt-in provider integration
  - **目前狀態**：`waiting_for_core_interface`；workspace 尚無 Core T50 Python `SourceCollector` Port/DTO 或 shared assertion implementation。Provider 已依 frozen SourceCollector `1.0.0` 建立 internal mapping boundary，未建立 Core substitute、未修改 shared contract；因此保持未完成。
  - **已通過 evidence（2026-08-01）**：`python -m unittest discover -s tests/contract -p test_pa70*.py -v` → 34 tests passed；涵蓋 `CT-COLLECT-COLLECT-01`、`CT-COLLECT-HEALTH-01`、`CT-COLLECT-CAPABILITIES-01`、3 valid/3 invalid schema examples、static/RSS/Playwright、SSRF/DNS rebinding/redirect/payload/robots/rate/deadline、typed error、zero hidden retry、record/replay、Grounding recollection與safe events。
  - **Provider integration evidence（2026-08-01）**：`python -m unittest discover -s tests/integration/providers -p test_pa70*.py -v` → OK，1 skipped；`PA70_LIVE_INTEGRATION=1` 才可外呼，normal CI no-live-by-default。本輪無 live network/AWS/write。
  - **版本／policy evidence**：contract `1.0.0`；provider `collector-adapter-1.0.0`；service boundary `https-rss-playwright-boundary-1.0.0`；security `collector-security-1.0.0`；connect/read/static/Playwright=`3s/10s/15s/30s`；Grounding discovery default `10s`（max `15s`）；orchestrator retry owner、adapter max attempts `1`、hidden retry `false`；operation replay returns recorded result。
  - **安全 evidence**：HTTPS/443、URL≤2048、redirect≤3 且逐跳重驗、public pinned peer IP、private/loopback/link-local/reserved/multicast/metadata拒絕、robots+allowlist、raw≤5MiB、clean≤1MiB、per-host concurrency 2/interval≥1000ms、safe event redaction均由 offline tests 驗證。OQ-B013 只阻塞真實 live-extension slice；本 slice 未宣稱該 production readiness。

- [ ] **PA71 — EvidenceExtractor adapter**
  - **OWNER: PROVIDER_COLLABORATOR**
  - **Core reference/dependency**：T71 reference；Core T50
  - **需求**：PA-REQ-*、PA-EXT-*
  - **允許修改**：`src/crypto_trust_agent/infrastructure/extraction/`、自身 capability 的 `src/crypto_trust_agent/infrastructure/aws/` module、`tests/contract/` provider harness/fixtures/evidence、`tests/integration/providers/`
  - **驗收**：bounded input；schema-constrained claims；禁止 system lineage fields；Core-controlled one repair≤20s；quarantine/error mapping；Guardrails/redaction。
  - **測試**：Shared Contract、schema fuzz、injection/security、timeout、opt-in provider integration
  - **目前狀態**：`offline_v2_complete_live_pending`；EvidenceExtractor v2 provider binding 與 authoritative successful repair 已完成離線驗證，但真實 Nova/AWS live invoke/probe evidence 尚未具備，因此不得勾選 `[x]`。
  - **v1 compatibility evidence（2026-08-01）**：frozen `EvidenceExtractor 1.0.0` 與 `CT-EXTRACT-REPAIR-01` 保持 quarantine-only，既有 `NovaLiteEvidenceExtractor` 未升級或改變 successful-repair 語意；`$env:PYTHONPATH='src'; python -B -m unittest tests.contract.test_pa71_evidence_extractor -v` → 21 passed。
  - **v2 contract/provider evidence（2026-08-01）**：新增獨立 `NovaLiteEvidenceExtractorV2`，原樣執行 Core `EvidenceExtractorV2ContractAssertions`；`$env:PYTHONPATH='src'; python -B -m unittest tests.contract.test_pa71_evidence_extractor_v2 -v` → 20 passed。涵蓋 repair contract `2.0.0`、output `1.0.0`、`CT-EXTRACT-REPAIR-02`、固定 `repair-authorization-2.0.0` hash、inline/locator authoritative content、UTF-8 1,048,576-byte bound與 SHA-256、asset/taxonomy/quote grounding、禁止 provider system-lineage、typed errors/redaction、authorization-first replay/conflict、late-result isolation與 successful repair。
  - **deadline/concurrency evidence**：repair 使用 receiver-local aggregate≤20s deadline、adapter/provider max attempts `1`、hidden retries `0`。Per-identity single-flight 只允許一次 provider call，不序列化 unrelated operations；leader deadline 建構例外必定完成 terminal result並釋放 event；follower 使用自身 deadline，等待逾時不得取得稍後發布的 leader result，後續 replay仍回傳 leader terminal object。兩項 reviewer-driven regressions皆先重現失敗再修正，最終 semantic reviewer複核為 no findings。
  - **Provider integration scaffold evidence（2026-08-01）**：`$env:PYTHONPATH='src'; python -B -m unittest tests.integration.providers.test_pa71_live_opt_in -v` → 4 passed、0 skipped。No-live-by-default、缺少 explicit opt-in時 fail closed；injected `RecordingInvoker` 驗證 `ExplicitLiveNovaClient.invoke()`/`probe()` delegation、`max_attempts=1`、`hidden_retries=0` 與 v2 successful repair binding。此為離線 scaffold evidence，**不是**真實 Nova/AWS success。
  - **完整離線回歸（2026-08-01）**：full contract 195 passed；architecture 4 passed；provider integration 40 passed、2 skipped（僅既有 PA70/PA72 live opt-in，PA71 0 skipped）；`python -B -m compileall -q src tests` 與 `git diff --check` 通過。所有預設驗證均未呼叫 network/AWS、未讀取 credentials。Ruff 非最低驗證要求且環境未安裝（`No module named ruff`）。
  - **安全／authority evidence**：authoritative locator 僅透過 injected `AuthoritativeContentResolver`，adapter 不自行存取任意 URL；provider raw output 在 DTO mapping 前 fail closed，SDK type、未知欄位、system lineage、越界 asset/taxonomy、ungrounded quote與 provider diagnostics均不得穿越 boundary；unknown exception固定安全映射 `unexpected_provider_error`。Core v2 Port/DTO/schema/shared assertions已足以表達本 slice，未修改 Core contract且不需建立 `PORT_CHANGE`；PA72 未修改。
  - **live dependency（未滿足）**：仍缺 deployment model ID、AWS Region、受控 credentials/access、符合 one-attempt/zero-hidden-retry 的 concrete live runtime invoker，以及真實 Nova invoke/probe 成功 evidence。本輪未獲 live 授權且未執行真實 AWS 呼叫；完成前 PA71 保持未勾選與 `offline_v2_complete_live_pending`。

- [ ] **PA72 — MarketRegimeProvider adapter**
  - **OWNER: PROVIDER_COLLABORATOR**
  - **Core reference/dependency**：T72 reference；Core T53
  - **需求**：PA-REQ-*、PA-MR-*
  - **允許修改**：`src/crypto_trust_agent/infrastructure/market_regime/`、自身 capability 的 `src/crypto_trust_agent/infrastructure/aws/` module、`tests/contract/` provider harness/fixtures/evidence、`tests/integration/providers/`
  - **驗收**：feature/version/alignment/hash validation；canonical Decimal；probability/anomaly schema+sum；≤25s cold-start bound；zero retry；safe error mapping；不得生成 fallback probabilities/AnalysisResult。
  - **測試**：Shared Contract、invalid distribution、timeout/cancellation、latency rehearsal、opt-in provider integration
  - **目前狀態**：`incomplete_live_evidence`；PA72 adapter 與離線 conformance 已完成，但未獲授權執行真實 SageMaker endpoint invoke/probe，因此不得勾選 `[x]`。
  - **離線 shared/contract evidence（2026-08-01）**：`python -B -m unittest tests.contract.test_pa72_market_regime tests.integration.providers.test_pa72_live_opt_in -v` → 23 tests passed；原樣執行 `MarketRegimeContractAssertions`，包含 `CT-MARKET-INFER-01`、`CT-MARKET-HEALTH-01`。涵蓋 formal adapter 必須顯式注入非 `None` `SageMakerClient`（缺少 client 在 inference 前 fail closed，無 synthetic result/probability）、顯式注入 `StubSageMakerClient` 的 shared harness、configured feature schema/order/calculation version、alignment、opaque hash identity/binding（未自創 hash algorithm）、expected/response model binding、provider JSON number 直接 Decimal parse/canonicalization、probability sum/anomaly/quality/limitations、request/response binding、receiver-local monotonic deadline、timeout/cancellation/latency rehearsal、endpoint unavailable、unknown redaction、exact replay conflict、zero retry、no fallback/no `AnalysisResult`。
  - **Provider integration scaffold evidence（2026-08-01）**：同上 integration test 通過 no-live-by-default、credential-independent fail-closed、explicit opt-in delegation 與 invoker retry-policy rejection；`PA72_SAGEMAKER_LIVE_INTEGRATION=1` 才可建立 live boundary。使用 injected `RecordingInvoker`，**不是**真實 SageMaker success；本輪未讀 AWS credential、未連 AWS、未 invoke/probe 真實 endpoint、未執行 network/write。
  - **工具 evidence（2026-08-01）**：PA72-scoped `python -m ruff check ...` → all checks passed；`MYPYPATH=src python -m mypy --namespace-packages --explicit-package-bases --follow-imports=skip --ignore-missing-imports ...` → no issues in 3 source files；`python -B -m pytest tests/contract/test_pa72_market_regime.py tests/integration/providers/test_pa72_live_opt_in.py -q` → 23 passed；`python -B -m compileall -q ...`、IDE diagnostics、`git diff --check -- ...` → passed。
  - **版本／policy evidence**：contract/schema `1.0.0`；provider `sagemaker-market-regime-adapter-1.0.0`；service boundary `sagemaker-runtime-boundary-1.0.0`；default model name/version `market-regime-xgboost`/`v1`；infer≤25000ms、health≤3000ms；Core retry/fallback owner；adapter/invoker max attempts `1`、hidden retries `0`；same operation + identical full request/hash/model returns recorded object，changed content returns typed conflict without I/O。
  - **安全/error evidence**：production adapter constructor 不再隱式建立 stub，必須顯式注入 `SageMakerClient` 且 `None` 在 inference 前 fail closed；`StubSageMakerClient` 僅作顯式注入的 non-production fixture。SDK types/request/response/exception、endpoint name/region、credential/token、raw provider payload與 vendor metadata 不進 Core DTO/event/error/log；infer/health 的 unknown exception 或 unknown `ProviderFailure(retryable=True)` 固定 `unexpected_provider_error` + Core `UNEXPECTED` + `retryable=false` + empty details，不傳播 vendor retryability或內容；method allowlist 內 code 才保留 frozen policy retryability。timeout→`market_regime_timeout`；endpoint failure→`endpoint_unavailable` typed error，health 不使用 `unavailable` status；provider limitations require local allowlist；不存在 fallback/synthetic probability 或 `AnalysisResult` construction。
  - **未滿足 live dependency**：仍需人工授權的受控 AWS credentials、deployment endpoint/region/model identity、符合 one-attempt/zero-hidden-retry 的真實 runtime invoker，以及成功 endpoint invoke/probe evidence。完成前 PA72 保持未完成。

- [ ] **PA73 — ReasoningProvider adapter**
  - **OWNER: PROVIDER_COLLABORATOR**
  - **Core reference/dependency**：T73 reference；Core T61
  - **需求**：PA-REQ-*、PA-RSN-*
  - **允許修改**：`src/crypto_trust_agent/infrastructure/reasoning/`、自身 capability 的 `src/crypto_trust_agent/infrastructure/aws/` module、`tests/contract/` provider harness/fixtures/evidence、`tests/integration/providers/`
  - **驗收**：bounded task-scoped Context；no network/DB/object-store/secret tools；Core 控制 repair/fallback；schema output 通過 shared citation/reference graph assertions（Fact→Evidence/Analysis、Inference→Fact、Conclusion→Fact/Inference，missing/cross-task/quarantined refs 拒絕）；不回 chain-of-thought；Guardrails/error redaction。
  - **測試**：Shared Contract、context bounds、prompt injection、timeout/cancellation、opt-in provider integration

- [ ] **PA74 — Persistence / event / artifact / observability adapters**
  - **OWNER: PROVIDER_COLLABORATOR**
  - **Core reference/dependency**：T74 reference；Core T32/T62
  - **Phase-specific gate**：OQ-B012 必須在 production persistence adapter、DynamoDB transaction/key/index 或正式 distributed concurrency/quota/idempotency integration slice 開始前核准；event、artifact 與 observability slices 不依賴 OQ-B012。
  - **需求**：PA-REQ-*、PA-AWS-*
  - **允許修改**：`src/crypto_trust_agent/infrastructure/{persistence,events,artifacts,observability}/`、自身 capability 的 `src/crypto_trust_agent/infrastructure/aws/` modules、`tests/contract/` provider harness/fixtures/evidence、`tests/integration/providers/`
  - **驗收**：atomic rate/idempotency；`ExecutionRepository.acquire_quota_and_create` 唯一完成 preflight revalidate + Task input lock + quota + Execution create + pass consume，`TaskRepository` 無獨立 lock；failure/stale/expired/rollback 不耗 quota；conditional transition；immutable Evidence/append history/strong snapshot page，含 offset/token TTL shared assertions；event dedup/redaction；artifact hash/idempotency/Manifest last/self-exclusion/cross-field assertions；least privilege/encryption/safe redrive。
  - **測試**：Shared Contract、concurrency/fault injection、unknown outcome replay、security/IAM review、opt-in provider integration

## Completion rule

每個 PA task 只有在 checkbox 下記錄 shared contract IDs/result、provider integration command/result、versions、timeout/retry config、安全 evidence 後才可完成。若需改 shared contract，保持未完成並提交 Port change proposal；不得由 Provider 直接修改 expected semantics。
