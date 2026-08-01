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
  - **目前狀態**：`incomplete`；PA71 review blockers 已完成 quarantine-only remediation，但 Core Port 缺口與真實 Nova live evidence 尚未解除，因此不得勾選 `[x]`。
  - **離線 remediation evidence（2026-08-01）**：`python -B -m unittest tests.contract.test_pa71_evidence_extractor tests.integration.providers.test_pa71_live_opt_in -v` → 23 tests passed；Core-owned `EvidenceExtractorContractAssertions` 原樣通過 `CT-EXTRACT-EXTRACT-01`、`CT-EXTRACT-REPAIR-01`、`CT-EXTRACT-HEALTH-01` 與 frozen schema examples。新增 regression 覆蓋 no-claims original repair 對 `ETH`、`unapproved_event`、ungrounded quote 一律 typed `quarantined`/zero claims、不得以 first original claim 推導 authority、provider validation diagnostic redaction，以及 health allowlisted/timeout/unknown mapping。
  - **其他離線檢查（2026-08-01）**：`python -B -m unittest discover -s tests/architecture -p test_*.py -v` → 4 passed；PA71 scoped `python -B -m compileall -q ...`、IDE diagnostics、`git diff --check` 通過。所有驗證皆未呼叫 network/AWS、未讀取 credential。
  - **版本／policy evidence**：contract/schema `EvidenceExtractor 1.0.0`；provider adapter `nova2-lite-extractor-adapter-1.0.0`；model boundary default `nova-2-lite`。Extract≤60000ms、repair≤20000ms、health≤3000ms；Core 是 retry/repair owner；adapter max attempts `1`、hidden retries `0`；same operation+same request replay recorded result，changed request conflict；同 raw/hash/context 最多一個 repair operation。
  - **quarantine-only Port limitation / exact contract gap**：frozen `RepairRequestDTO 1.0.0` 只有 `original_result`、`validator_errors`、hash/policy/deadline，沒有 authoritative cleaned content/locator、approved assets 或 allowed event taxonomy；因此 adapter 無法證明 repair quote grounding、asset scope 或 taxonomy scope，也不得從 original first claim 推導 authority。安全優先下，任何帶 claims／`valid` 的 repair provider result 均轉成 fixed local `repair_scope_unavailable` typed `quarantined` 且 claims 為空。Frozen shared assertions 本輪未直接衝突並仍通過，但只驗 configured quarantine/typed failure，未提供或驗證 authoritative successful-repair scope；在 Core Port/DTO/schema 經 authority 核准前，PA71 不能宣稱完整 successful repair capability。
  - **redaction／health evidence**：provider-authored validation `path`/`code`/`safe_message` 不進 DTO/event，改為 bounded local fixed values；`Authorization: Bearer TOP-SECRET`、vendor exception/secret/raw prompt regression 均證明不外洩。Extraction 與 health 的 unknown exception／unknown ProviderFailure 均固定回 `unexpected_provider_error`、Core `PortErrorCategory.UNEXPECTED`、空 details；Health 僅 `TimeoutError`/allowlisted `extractor_timeout` 映射 timeout，`extractor_unavailable` 與其他 method-allowlisted ProviderFailure code保留。
  - **live dependency（未滿足）**：offline opt-in scaffold 透過 injected `RecordingInvoker` 實際走過 `ExplicitLiveNovaClient.invoke()` 與 `probe()`，且 no-opt-in fail closed；此結果只證明 composition/boundary delegation，**不是**真實 Nova/AWS integration success。仍需另行授權 live AWS、受控 credentials、deployment model/region、真實 runtime invoker 與成功 provider call/probe evidence；本輪未獲授權且未執行，故 PA71 保持 incomplete。

- [ ] **PA72 — MarketRegimeProvider adapter**
  - **OWNER: PROVIDER_COLLABORATOR**
  - **Core reference/dependency**：T72 reference；Core T53
  - **需求**：PA-REQ-*、PA-MR-*
  - **允許修改**：`src/crypto_trust_agent/infrastructure/market_regime/`、自身 capability 的 `src/crypto_trust_agent/infrastructure/aws/` module、`tests/contract/` provider harness/fixtures/evidence、`tests/integration/providers/`
  - **驗收**：feature/version/alignment/hash validation；canonical Decimal；probability/anomaly schema+sum；≤25s cold-start bound；zero retry；safe error mapping；不得生成 fallback probabilities/AnalysisResult。
  - **測試**：Shared Contract、invalid distribution、timeout/cancellation、latency rehearsal、opt-in provider integration

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
