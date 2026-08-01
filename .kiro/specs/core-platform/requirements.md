# Core Platform Requirements

- **規格版本**：0.2.0
- **來源基線**：`CRYPTO_TRUST_AGENT_MASTER_SPEC.md` v0.5 + `docs/architecture/source-confidence-register.md`
- **狀態**：Approved；規格已核准，產品實作仍須依 `tasks.md` 執行
- **範圍**：核心 Domain/Application、Formal Run orchestration、API/use-case boundary、artifact 與外部 provider contracts；不包含外部 adapter 的具體實作
- **契約權威**：`docs/architecture/ports-and-schemas.md` 為索引；`docs/architecture/schemas/**/contract.schema.json` 為 Draft 2020-12 欄位級 authority

> Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 `SOURCE_TEXT_UNCERTAIN`。ADR-001..010 已由 Maintainer 人工核准。

## 1. EARS 表達規則

本文使用 EARS（Easy Approach to Requirements Syntax）：

- **Ubiquitous**：系統應……
- **Event-driven**：當……時，系統應……
- **State-driven**：當系統處於……狀態時，系統應……
- **Unwanted behavior**：若……，則系統應……
- **Optional feature**：在……被啟用時，系統應……
- **Complex**：在……狀態下，當……時，系統應……

`應` 表示可驗收義務；Open Questions 不構成已核准需求。

## 2. 詞彙

- **Task**：已驗證且已規劃的使用者分析請求；建立 Task 不等於開始 Formal Run。
- **Execution**：一次 Formal Run 嘗試；只有 Pre-flight 通過後才能建立。
- **Evidence**：具來源、時間、hash、raw locator 與 lineage 的不可變證據。
- **AnalysisResult**：由確定性公式或版本化模型產生、可追溯輸入的分析結果。
- **Formal Run**：受 900 秒 hard deadline 與正式額度限制的完整執行。
- **Pre-flight**：不消耗正式額度的 readiness 檢查。
- **Required source**：缺席導致 Execution `partial` 並強制揭露。
- **Required-if-available source**：有 Collector 且預算足夠就必須嘗試；缺席只產生 limitation。
- **Optional source**：預算不足可 `skipped`。
- **External implementation**：collector、Nova、SageMaker、AgentCore 與 AWS adapter；只可透過 Port 接入核心。

## 3. 功能需求

### FR-001 任務建立、身分、冪等與限流

- **CP-FR001-01（Ubiquitous）**：系統應只接受 `question`、`assets`、`timeframe` 與 `formal_run` 作為 Task 建立的業務輸入。
- **CP-FR001-02（Unwanted）**：若正式環境無法從已驗證 Cognito JWT claim 建立可信 `user_id`，則系統應拒絕 Task 建立。
- **CP-FR001-03（Unwanted）**：若 request body、query string 或一般自訂 header 提供 `user_id`，則系統不得以該值建立、查找或授權 Task。
- **CP-FR001-04（Event）**：當輸入通過驗證時，系統應依版本化 canonicalization 規則正規化 question、assets、timeframe，並計算 `request_fingerprint`。
- **CP-FR001-05（Event）**：當同一 `user_id` 在 24 小時內提交相同 `request_fingerprint` 時，系統應回傳既有 `task_id`、標示 `reused`，且不得建立新 Task。
- **CP-FR001-06（Event）**：當合法請求未命中冪等紀錄時，系統應以原子 create-or-get 語意建立唯一 Task 並標示 `created`。
- **CP-FR001-07（Ubiquitous）**：每次 Task 建立請求（包含冪等命中）應計入該 `user_id` 每小時 10 次的獨立限流。
- **CP-FR001-08（Unwanted）**：若 Task 建立限流已超過每小時 10 次，則系統應回傳 429，且不得建立 Task、改變正式額度或執行冪等副作用。
- **CP-FR001-09（Unwanted）**：若資產、時間範圍或題型不合法或不支援，則系統應回傳 422 且不得建立 Task。
- **CP-FR001-10（State）**：當 Task 尚未通過 Formal Run Pre-flight 時，系統不得建立正式 `execution_id`。
- **CP-FR001-11（Event）**：當 Task 建立完成時，系統應記錄 `request_fingerprint`、`fingerprint_ruleset_version`、`idempotency_outcome`、解析後 `task_id` 與限流結果，且只記錄使用者穩定假名。

### FR-002 題型、Deterministic Planner 與來源需求

- **CP-FR002-01（Ubiquitous）**：系統應支援 `market_status`、`hypothesis_validation`、`asset_comparison` 三種題型。
- **CP-FR002-02（Event）**：當 Task 輸入合法時，Deterministic Planner 應產生 answer dimensions、固定 source categories、固定 query strings、各類時間預算、warm-up range、reporting range、Sourcing Plan 與 Analysis Plan。
- **CP-FR002-03（Ubiquitous）**：Planner 應為純確定性邏輯，不得呼叫 LLM、Nova、Opus、Sonnet 或 Web Grounding。
- **CP-FR002-04（Ubiquitous）**：官方資料集應永遠列為 required market source。
- **CP-FR002-05（Event）**：當 Planner 建立 Analysis Plan 時，系統應套用下列 `source_requirement_matrix`：

| 題型 | market | news | official | on_chain | social | macro |
|---|---|---|---|---|---|---|
| market_status | required | required | required | optional | optional | optional |
| hypothesis_validation | required | required | required | required_if_available | optional | optional |
| asset_comparison | required | required | required | optional | optional | optional |

- **CP-FR002-06（Unwanted）**：若 required source 缺席，則系統應繼續產生可用輸出、將 Execution 標為 `partial`，並在 Final Report limitation 強制揭露。
- **CP-FR002-07（Event）**：當 required-if-available 類別存在 Collector 且剩餘類別預算足夠時，系統應嘗試蒐集；若缺席，應記錄 limitation 且不得僅因此標為 `partial`。
- **CP-FR002-08（State）**：當 optional source 的預算不足時，系統得不嘗試，但應明確標為 `skipped`。
- **CP-FR002-09（Ubiquitous）**：Nova、Reasoning Provider 與 Web Grounding 不得修改 source requirement、query、budget 或 plan branch。

### FR-003 多源蒐集

- **CP-FR003-01（Ubiquitous）**：系統應能透過 `SourceCollector` Port 規劃 market、news、official、on_chain、social、macro 類別。
- **CP-FR003-02（Event）**：當 Sourcing Plan 包含來源工作時，每個工作應收斂為 `success`、`skipped` 或 `failed`，不得無結果消失。
- **CP-FR003-03（Event）**：當 Collector 工作結束時，系統應記錄安全化參數、開始/結束時間、duration、retry count、source locator、結果摘要及 typed error。
- **CP-FR003-04（Unwanted）**：若 optional source 失敗，則系統應繼續執行，並記錄 failure 與 limitation。
- **CP-FR003-05（State）**：當題型為雙資產比較時，系統應平行觸發兩資產 Collector；若在蒐集 hard deadline 前無法完整覆蓋 required sources，應優先完整覆蓋輸入中的第一資產並將第二資產標為 `partial`。

### FR-004 Raw fetch 與安全網頁蒐集

- **CP-FR004-01（Ubiquitous）**：Collector 應優先使用 RSS、官方 API 或靜態 HTTP，僅對 JavaScript 動態來源使用 Playwright。
- **CP-FR004-02（Event）**：當成功抓取網頁來源時，系統應保存 URL、canonical URL、HTTP status、`published_at`（若可得）、`fetched_at`、正文或安全 raw reference、content hash 與 raw locator。
- **CP-FR004-03（Ubiquitous）**：Collector 應遵循來源條款、robots、rate limit、domain allowlist、DNS/IP 驗證、SSRF 防護、redirect/payload/timeout 上限。
- **CP-FR004-04（Unwanted）**：若 URL 未通過 allowlist 或 DNS/IP/SSRF 驗證，則系統應拒絕 fetch，記錄安全化拒絕原因，且不得讓 Nova 或 Web Grounding 繞過拒絕。
- **CP-FR004-05（Event）**：當 Web Grounding 回傳 URL 時，系統應將其視為 discovery result，並在自有 Collector 驗證及抓取成功後才建立 Raw Record。
- **CP-FR004-06（Ubiquitous）**：外部內容應視為不可信資料，不得覆蓋 system/developer instruction 或 orchestration plan。

### FR-005 Evidence Pipeline

- **CP-FR005-01（Ubiquitous）**：每筆 Evidence 應符合版本化 schema，並在建立後保持不可變。
- **CP-FR005-02（Ubiquitous）**：每筆 Evidence 應包含 source、`published_at`（nullable）、required `fetched_at`、結構化 `ContentReferenceDTO`、raw/clean SHA-256、canonical `raw_locator`、query provenance、Task/Execution/RawRecord lineage 與 `validation_status=active|quarantined`。
- **CP-FR005-03（Ubiquitous）**：Evidence 不得包含 claim stance、Trust、relevance、freshness、independence 或其他可變評分；legacy S3-specific locator 欄位只可由 migration/adapter mapper 讀入，Core 不 dual-write。
- **CP-FR005-04（Event）**：當 Evidence 與 Claim 建立關聯時，系統應建立 `EvidenceClaimLink`，其 stance 僅可為 `supports`、`contradicts`、`context`，並允許多對多關聯。
- **CP-FR005-05（Event）**：當評估 Evidence 時，系統應 append 新 `EvidenceAssessment`，保留 per-evidence 單調遞增 `assessment_sequence`、`assessment_version`、`computed_at` 與 `ruleset_version`，不得覆寫舊版本。
- **CP-FR005-06（Event）**：當建立 Reasoning Context 或 Final Report 時，系統應以最大 `assessment_sequence` 選擇 latest 合格 assessment，並在 Execution Log 記錄實際 `assessment_id` 與版本；不得以 timestamp 猜 latest。
- **CP-FR005-07（Unwanted）**：若 Evidence 缺少 lineage、跨 Task、引用不存在或被 quarantine，則系統應阻止其進入 Reasoning Context 與 Final Report。
- **CP-FR005-08（Ubiquitous）**：`fetched_at`、hash、raw locator、HTTP metadata、link ID 與 assessment sequence/version 應由程式產生或驗證，不得由 LLM 虛構。
- **CP-FR005-09（Ubiquitous）**：Evidence list 應採 task-scoped strong snapshot pagination；page limit default 50、minimum 1、maximum 200，cursor/token 必須 opaque；相同 entity ID + 相同 canonical payload 為 no-op，相同 ID + 不同 payload 為 conflict。

### FR-006 去重與來源獨立性

- **CP-FR006-01（Event）**：當評估內容重複性時，系統應使用 canonical URL、content hash、標題相似度、事件主體與時間聚類。
- **CP-FR006-02（Event）**：當多篇內容屬於同一通訊社稿件或符合版本化相似度及事件窗口規則時，系統應將其歸入同一 `independence_group`。
- **CP-FR006-03（Ubiquitous）**：同一 independence group 的重複內容不得增加 corroboration count。
- **CP-FR006-04（Ubiquitous）**：系統應使用版本化 press-agency/domain allowlist，並在 `EvidenceAssessment.computation_ruleset_version` 保留所用規則版本。
- **CP-FR006-05（Event）**：當規則重算時，系統應新增 assessment，不得修改 Evidence 或既有 assessment。

### FR-007 市場分析與 Market Regime

- **CP-FR007-01（Ubiquitous）**：核心應以版本化確定性公式計算 return、high/low、volume change、volatility、drawdown、SMA、trend 與 deterministic regime。
- **CP-FR007-02（Ubiquitous）**：每個 AnalysisResult 應包含輸入來源/期間、data quality、calculation/model version、`computed_at` 與可引用 ID。
- **CP-FR007-03（Ubiquitous）**：Core wire 的所有精確數值應使用 ADR-004 canonical decimal string（precision 38、scale 18），以 UTC 對齊；禁止 scientific notation、`+`、leading zero、trailing fractional zero 與 `-0`。Master/provider JSON number 只能在 boundary 直接以 Decimal parser 轉換，不可先經 binary float。return 可為負；price/volume 非負；score/probability/anomaly/confidence 應在 `[0,1]`；probability distribution 總和容許差為 `0.000001`。
- **CP-FR007-04（Ubiquitous）**：系統不得直接比較不同資產的 base-asset volume 絕對值。
- **CP-FR007-05（Event）**：當 reporting range 超過 2026-05-31 時，系統應啟用合格 live extension，並保留逐點 `official_dataset` 或 `live_extension` provenance 與 `transition_date`。
- **CP-FR007-06（Unwanted）**：若 live extension 有缺口，則系統不得以前一筆官方值靜默填補；應記錄 limitation，必要 market coverage 不足時標記 `partial`。
- **CP-FR007-07（Unwanted）**：若官方 dataset metadata、檔案、schema、日期或 OHLCV readiness 失敗，則 Formal Run 應失敗，且不得改用其他資料取代官方基準。
- **CP-FR007-08（Event）**：當呼叫 MarketRegimeProvider 時，單次 timeout 應含冷啟動且初始上限 25 秒；若失敗或逾時，Adapter 應只回 typed error、不得重試或生成 fallback probabilities；Core 應建立版本化 deterministic fallback `AnalysisResult`，保存公式/ruleset、source refs、quality/limitations 並揭露模型缺席。

### FR-008 Trust、Evidence independence 與 Contradiction

- **CP-FR008-01（Ubiquitous）**：系統應分開計算 source trust、evidence relevance、freshness、independence 與 consistency。
- **CP-FR008-02（Ubiquitous）**：系統應偵測 `numeric`、`temporal`、`source`、`narrative`、`signal`、`status` conflict。
- **CP-FR008-03（Event）**：當發現矛盾時，系統應保留雙方引用、衝突類型與規則版本，並使矛盾影響 final confidence。
- **CP-FR008-04（Ubiquitous）**：final confidence 應可拆解、可重算，且不得以單一不透明 LLM 分數取代。
- **CP-FR008-05（Ubiquitous）**：系統應保留可取得的 counter-evidence；若未找到，應揭露已搜尋的範圍。

### FR-009 Structured Reasoning

- **CP-FR009-01（Ubiquitous）**：Reasoning Provider 應只接收已驗證、task-scoped、有大小上限且不含 secret/raw HTML 的 Structured Reasoning Context。
- **CP-FR009-02（Ubiquitous）**：每個 Fact 應引用有效 Evidence ID 或 Analysis ID。
- **CP-FR009-03（Ubiquitous）**：每個 Inference 應引用一個以上 Fact ID。
- **CP-FR009-04（Ubiquitous）**：每個 Conclusion 應引用一個以上 Fact ID 或 Inference ID。
- **CP-FR009-05（Event）**：當 Opus output 驗證失敗時，系統應最多執行一次 repair，且 repair 固定上限 60 秒。
- **CP-FR009-06（Unwanted）**：若 Opus repair 失敗或逾時，則系統應切換 Sonnet 5，且不得第二次重試 Opus。
- **CP-FR009-07（Unwanted）**：若 Sonnet 5 output 仍無法通過 schema、citation 或 numeric validation，則系統應安全結束且不得寫入未驗證報告。
- **CP-FR009-08（Unwanted）**：若引用不存在，則系統應拒絕 output；該修復應包含於同一次 60 秒 repair 額度，不得另開預算。

### FR-010 Final Report、Evidence List、Execution Log 與 Manifest

- **CP-FR010-01（Event）**：當正式分析通過 publication validation 時，系統應產生 Final Report JSON、Markdown、HTML。
- **CP-FR010-02（Event）**：當正式分析完成時，系統應產生 Evidence List JSON、CSV 與 Execution Log JSONL。
- **CP-FR010-03（Event）**：當所有可用 artifact 寫入完成時，系統應最後產生 Manifest，列出 SHA-256、MIME type、`schema_version` 與 `generated_at`。
- **CP-FR010-04（Ubiquitous）**：Final Report 應包含執行摘要、直接回答、market judgment、Facts、Inferences、支持/反對證據、一致性、Contradictions、confidence 組成、限制、推翻條件、watchpoints 與投資免責聲明。
- **CP-FR010-05（Event）**：當 reporting range 超過 2026-05-31 時，摘要應揭露 `transition_date`，且已確認事實與 Evidence List 應逐筆標示 market data provenance。
- **CP-FR010-06（Unwanted）**：若 renderer 失敗但 canonical JSON 成功，則系統應保留 canonical artifact、將 Execution 標記 `partial` 並記錄缺少格式。
- **CP-FR010-07（Ubiquitous）**：所有最終結論與重要事實應能從 artifact 追溯至 Evidence 或 Analysis ID。
- **CP-FR010-08（Ubiquitous）**：Execution Log 不得包含 secret、token、完整認證 header、未遮罩 PII、不必要的完整 prompt 或完整敏感 raw content。
- **CP-FR010-09（Unwanted）**：若 degraded publication 缺少 Final Report JSON、Evidence List JSON、Execution Log JSONL 或 Manifest JSON 任一最低項目，系統不得 publish；Markdown/HTML/CSV renderer failure 得形成 `partial`，Manifest 應最後寫入、列 available/missing/reason 且不自列。

### FR-011 Formal Run Pre-flight、額度與 orchestration

- **CP-FR011-01（Event）**：當使用者請求 Formal Run 前，系統應先驗證資產/時間/題型、官方 dataset readiness，並對 extraction/reasoning、MarketRegime 與外部 allowlist API 執行不含完整推論的輕量健康探測。
- **CP-FR011-02（Ubiquitous）**：Pre-flight 不應消耗正式執行額度；失敗、過期或超限均不得 consume quota。
- **CP-FR011-03（Unwanted）**：若任一必要依賴不健康，則系統不得建立 `execution_id`，並應提供安全、具體、可重試的 readiness reason。
- **CP-FR011-04（Ubiquitous）**：Pre-flight 應採每 task 每分鐘最多 3 次的獨立限流。
- **CP-FR011-05（Unwanted）**：若 Pre-flight 超限，則系統應回 429 與安全的 retry-after，且不得觸發外部健康探測或消耗正式額度。
- **CP-FR011-06（Event）**：Pre-flight pass 應綁定 `task_id`、`task_version`、`input_lock_hash`、`dependency_snapshot_hash`，依 ADR-006 提案於固定 60 秒 TTL 到期且 single-use；當使用者啟動 Execution 時，系統應只透過 `ExecutionRepository.acquire_quota_and_create` 在單一原子操作中重驗 pass freshness/binding、鎖定 Task input、取得正式額度、建立 `execution_id` 並 consume pass。`TaskRepository` 不得提供獨立 execution lock；任何檢查失敗、stale 或 expired 均不得建立 Execution 或消耗 quota。
- **CP-FR011-07（Ubiquitous）**：正式額度應以 `(trusted_user_scope, request_fingerprint)` 控管，且獨立於 24 小時 Task idempotency 保存；新 `task_id` 不得重置額度。
- **CP-FR011-08（Ubiquitous）**：同一 quota scope 最多有 2 次正式 Execution：使用者原始執行一次及管理者授權技術重跑一次。
- **CP-FR011-09（Event）**：當管理者授權重跑時，系統應保留原紀錄並記錄 `original_execution_id`、`technical_failure_code` 與目前正式執行次數。
- **CP-FR011-10（Unwanted）**：若管理者重跑再次故障，則系統應轉人工個案處理且不得再透過 Formal Run 自動重跑。
- **CP-FR011-11（Ubiquitous）**：Formal Run 應遵守 900 秒不可延長 hard deadline，並保留推理、驗證與最低合格 artifact 寫入預算。
- **CP-FR011-12（State）**：當接近 stage deadline 時，系統應停止新增低優先工作，先完成 required sources、主要 Evidence、驗證、最低合格 artifact 與 log。
- **CP-FR011-13（Ubiquitous）**：UI 應顯示進度、剩餘時間、partial failure 與 artifact 狀態，並支援最終 Evidence/Artifact 檢視。

## 4. Port 與核心邊界需求

- **CP-PORT-01（Ubiquitous）**：Application 應定義並擁有 `TaskRepository`、`ExecutionRepository`、`EvidenceRepository`、`ArtifactRepository`、`EventPublisher`、`SourceCollector`、`EvidenceExtractor`、`MarketRegimeProvider`、`ReasoningProvider`、`Clock`。
- **CP-PORT-02（Ubiquitous）**：十個 Port 共 37 個 stable methods；每個 method 應在 Draft 2020-12 `contract.schema.json` 完整定義 request、success response、method-specific `ErrorResult` union、required/additional properties、enum/default/min/max/pattern/format、stable CT ID、`schema_version`，並以 `x-method-policy` 完整指定 timeout、retry owner、idempotency 與 concurrency；`docs/architecture/ports-and-schemas.md` 只作索引與 traceability。
- **CP-PORT-03（Ubiquitous）**：collector、extraction、market regime、reasoning 與 AWS integration 應視為 Infrastructure 實作，且不得把 SDK type 洩漏到 Application/Domain。
- **CP-PORT-04（Ubiquitous）**：每個 production adapter 與 fake adapter 應通過同一 Port contract-test suite；shared semantic assertions 應驗證 JSON Schema 無法完整表達的 Evidence offset/token TTL、Reasoning citation graph 與 Artifact Manifest 跨欄 invariant。Provider 只可在 `tests/contract/` 新增 adapter harness/fixtures/evidence，不得修改 shared schema/assertions/expected semantics；Core integration 只放 `tests/integration/core/`，真實 Provider/AWS integration 只放 `tests/integration/providers/`。
- **CP-PORT-05（Ubiquitous）**：核心應能只使用 fake adapters 完成三種題型與主要降級情境的本機 E2E。
- **CP-PORT-06（Unwanted）**：若 adapter/provider exception 無法映射為該 method 既定 error，adapter 應轉成 `unexpected_provider_error` 並保留 allowlisted safe diagnostics，不得洩漏 secret、raw exception 或 vendor object。
- **CP-PORT-07（Ubiquitous）**：Provider 遇到 Port gap 應建立 `docs/proposals/PORT_CHANGE_<name>.md`，包含 Problem、Proposed change、Compatibility impact、Testing requirements、SemVer impact，等待 Core Maintainer 核准；不得直接改 shared contract。

## 5. 架構與品質需求

- **CP-ARCH-01（Ubiquitous）**：Domain 不得 import FastAPI、Boto3、Bedrock、SageMaker、AgentCore、資料庫或其他 vendor SDK。
- **CP-ARCH-02（Ubiquitous）**：Application 只能依賴 Domain 與 Application-owned abstract Port/DTO。
- **CP-ARCH-03（Ubiquitous）**：Infrastructure 應實作 Application Port；Presentation 應只透過 use-case boundary 操作核心。
- **CP-ARCH-04（Ubiquitous）**：FastAPI request/response、dependency injection 與 HTTP status 不得進入 Domain。
- **CP-ARCH-05（Ubiquitous）**：所有時間應使用 UTC；同 process deadline 計算使用 injected Clock 的 monotonic time。跨 process 只傳 `DeadlineDTO(schema_version, operation_id, deadline_at_utc, budget_ms, sent_at_utc, safety_margin_ms)`，receiver 以自己的 monotonic clock 建 local deadline，不得傳遞或比較不同 runtime 的 monotonic epoch。
- **CP-ARCH-06（Ubiquitous）**：Boundary schema 與 planner/trust/formula/fingerprint ruleset 應分別版本化，breaking change 應有 migration 與相容策略。
- **CP-ARCH-07（Ubiquitous）**：正式發布前應驗證 schema、citation、numeric consistency、task lineage、artifact hash 與 manifest completeness。
- **CP-ARCH-08（Ubiquitous）**：所有外部呼叫與重試應有界；effective timeout 取 provider limit、budget 與 UTC remaining minus safety margin 的最小值，並由 Step Functions/Lambda/HTTP/SDK outer guards 強制不得超出 stage 或 900 秒 deadline。

## 6. 900 秒 deadline 驗收預算

| 階段 | Target | Hard deadline | 需求 |
|---|---:|---:|---|
| 輸入驗證/Task/題型 | 0:00–0:20 | 0:30 | 純程式，不外呼 |
| Plans | 0:20–1:30 | 2:00 | deterministic，不呼叫 LLM |
| 平行蒐集 | 1:30–6:00 | 7:30 | 可壓縮，先犧牲低優先來源 |
| Nova/Evidence/去重 | 3:00–8:00 | 8:30 | pipeline overlap；repair ≤20 秒 |
| deterministic analysis/SageMaker | 7:30–9:30 | 10:00 | SageMaker ≤25 秒、不重試 |
| Trust/Contradiction/Context | 9:30–11:00 | 11:30 | 使用已記錄 assessment |
| Opus/repair/fallback | 11:00–13:00 | 13:30 | repair ≤60 秒 |
| publication validation | 13:30–14:20 | 14:35 | 不接受未驗證 output |
| final writes/manifest/UI | 14:35–14:55 | 15:00 | Target 20 秒 + 5 秒 buffer |

各 stage buffer 不得任意跨階段挪用；只有經 Spec 更新與 Phase F 實測校準，才可在不放寬 900 秒的前提下重新切割。

## 7. 驗收追蹤

| 驗收主題 | 對應需求 | 最低測試層級 |
|---|---|---|
| 身分、Task 冪等、限流 | CP-FR001-* | Unit + Contract + API Integration + E2E |
| Planner 與 source matrix | CP-FR002-* | Unit/property + E2E |
| Collector outcomes/security | CP-FR003-*、CP-FR004-* | Contract + Integration + E2E |
| Evidence 分離/lineage | CP-FR005-*、CP-FR006-* | Unit + Repository Contract + Integration |
| 市場分析/live transition | CP-FR007-* | Unit + Integration + E2E |
| Trust/Contradiction | CP-FR008-* | Unit + E2E |
| Structured Reasoning/citation | CP-FR009-* | Contract + Integration + E2E |
| Artifact bundle | CP-FR010-* | Contract + Integration + E2E |
| Pre-flight/quota/deadline | CP-FR011-* | Unit + Contract + API Integration + E2E |
| Clean Architecture | CP-ARCH-* | Architecture tests |
| Provider substitutability | CP-PORT-* | Shared contract tests + fake E2E |

## 8. Decision gates

Blocking OQ-B001..B015 與 nonblocking OQ-N001..N005 的 description、source confidence、owner、proposed default、decision deadline、resolution ADR 與 status 只在 `docs/architecture/open-questions.md` 維護。

- 所有 blocking decisions 截止於 Core Phase 1（T10）之前；未取得 owner 人工核准前不得開始 Phase 1+。
- OQ-B004 的 Draft 2020-12 schemas 已通過結構與 examples 驗證，但仍等待 human schema review，不得自動標 Approved。
- 只有 `proposed_pending_human_approval`／`schema_validated_pending_human_approval` 等明確狀態可使用；實作、測試或文件存在不會自動解決問題。
- 只由不可可靠判讀文字推導的內容必須標 `SOURCE_TEXT_UNCERTAIN`，不得補猜答案。
