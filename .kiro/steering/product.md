# CryptoTrust Agent — Product Steering

## 文件定位與來源信心

本文件是產品決策基線；可靠來源、設計推導與待確認項目見 `docs/architecture/source-confidence-register.md` 與 `docs/architecture/open-questions.md`。

> Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 `SOURCE_TEXT_UNCERTAIN`。

ADR-001 至 ADR-010 已由 Maintainer 於 2026-08-01 人工核准。若後續發現與可靠 Master obligation 衝突，先更新 Spec 並取得人工決議，不得直接改產品程式。

## 產品使命

CryptoTrust Agent 將 BTC、ETH、SOL、BNB、XRP 的市場、新聞、官方公告、鏈上、社群與總體資料，轉換為可驗證、可回溯且揭露限制的分析。此使命敘述含不可可靠判讀中文來源的摘要成分，來源信心依 `PRODUCT-MISSION` 列標記 `SOURCE_TEXT_UNCERTAIN`；不得據此新增未由 FR／schema／驗收支持的功能。

## 支援情境

1. `market_status`：單一資產市場狀態分析。
2. `hypothesis_validation`：蒐集支持與反對證據以驗證假設。
3. `asset_comparison`：比較兩個支援資產的市場位置、流動性與風險特徵。

不支援的資產、模糊時間或不支援題型回傳 422，且不得建立 Task。

## 核心產品原則

- **Evidence-first**：重要事實與最終結論必須能追溯至 Evidence 或 Analysis ID。
- **Trust 與內容分離**：Evidence 不可變；Trust、relevance、freshness、independence 等可變結果只存在 append-only、版本化 `EvidenceAssessment`。
- **保留反證與矛盾**：counter-evidence、資料缺席與 numeric/temporal/source/narrative/signal/status conflict 不得被隱藏。
- **確定性優先**：Planner、來源需求、數值公式、狀態機、deadline、fallback `AnalysisResult` 與 artifact 驗證由 Core 控制。
- **有界模型權限**：EvidenceExtractor 只作 schema-constrained extraction；ReasoningProvider 只接收 bounded Structured Reasoning Context；provider 不決定來源或流程分支。
- **身分可信**：正式環境 identity 只取已驗證 Cognito JWT `sub`；issuer、audience、signature、expiry 均須驗證。管理者是 `cognito:groups` 內的 `CryptoTrustAdmins`。這些精確值仍待 ADR-002 人工核准。
- **先 readiness、後正式額度**：Pre-flight pass 依 ADR-006 提案綁定 task/version/input/dependency snapshot、有效 60 秒且 single-use；Execution create 原子重驗、鎖定、取得 quota、建立 Execution 並 consume pass。
- **完整 lineage**：Evidence 必須有 source、time、raw/clean hash、canonical `raw_locator`、結構化 content reference、query provenance 與 task/execution/raw-record lineage。
- **安全記錄**：不得記 prompt、secret、token、完整認證 header、未遮罩個資或完整敏感 raw content；使用版本化 keyed HMAC pseudonym。

## 來源需求基準

| 題型 | market | news | official | on_chain | social | macro |
|---|---|---|---|---|---|---|
| market_status | required | required | required | optional | optional | optional |
| hypothesis_validation | required | required | required | required_if_available | optional | optional |
| asset_comparison | required | required | required | optional | optional | optional |

- `required` 缺席：Execution 為 `partial`，Final Report 強制揭露。
- `required_if_available` 缺席：記錄 limitation，不單獨造成 `partial`。
- `optional`：時間不足可 `skipped`；每個規劃來源仍須有 success/skipped/failed 結果。

## 任務、冪等與正式執行

- Task 業務 input：`question`、`assets`、`timeframe`、`formal_run`；client identity 不屬 body/query/custom header。
- Fingerprint 依 ADR-001 的 `fingerprint-1.0.0` 提案產生；相同 trusted subject/fingerprint 在 24 小時內重用 Task，且冪等命中仍計入每小時 10 次限流。
- 正式額度以 trusted subject/fingerprint 控管，獨立於 Task 視窗；一般使用者一次，管理者僅可針對 allowlisted 技術故障授權一次重跑。
- Pre-flight 每 task 每分鐘最多 3 次；第 4 次回 429 且不得探測 provider。

## Decimal 與 market fallback

- Core wire 只接受 ADR-004 的 canonical decimal string；precision 38、scale 18。Master JSON number 只可在 boundary 直接以 Decimal parser 轉換，不可先經 binary float。
- return 可負；price/volume 非負；score/probability/anomaly/confidence 在 `[0,1]`；probability sum tolerance 為 `0.000001`。精確規則仍待人工核准。
- MarketRegimeProvider adapter 不得自行生成 fallback probabilities。timeout/unavailable/invalid output 時，由 Core 建立版本化 deterministic fallback `AnalysisResult`，保存公式／ruleset、source refs、quality/limitations 並揭露 provider absence。

## 正式執行輸出

Normal run 目標格式：Final Report JSON/Markdown/HTML、Evidence List JSON/CSV、Execution Log JSONL、Manifest JSON。

依 ADR-009 的 degraded minimum bundle 提案：

1. Final Report JSON
2. Evidence List JSON
3. Execution Log JSONL
4. Manifest JSON

缺任一 minimum artifact 不得 publish。Markdown/HTML/CSV renderer failure 可標 `partial`；Manifest 最後生成、列 available/missing/reason 且不自列。

Final Report 應包含直接回答、market judgment、Facts、Inferences、Conclusions、支持與反對證據、跨訊號一致性、Contradictions、可拆解 confidence、限制、推翻條件、watchpoints 與投資免責聲明。

## 官方市場資料

- 官方 CSV 是歷史市場分析共同基準，不得被外部資料覆寫。
- 資料為 UTC、USDT、日線，五資產各 1,826 筆，期間 2021-06-01 至 2026-05-31。
- reporting range 超過 2026-05-31 必須使用合格 live extension，揭露 `transition_date` 與逐筆 provenance；provider/credential/health 細節仍是 OQ-B013。
- 缺資料不得以最後一筆靜默填補；不得直接比較不同資產的 base-asset volume 絕對值。

## 產品成功條件

- 三種題型可由 fake adapters 完成本機 E2E。
- Formal Run 在 900 秒內完成，所有 major step 具 timestamp、duration、outcome。
- required/optional absence、model timeout、renderer failure 與 Core-owned fallback 依契約降級。
- Evidence/Claim/Assessment 分離，assessment ID/version 可由 log 回溯。
- schema、citation、numeric、lineage 與 artifact hash 通過驗證後才發布。
- Demo UI 顯示進度、剩餘時間、partial failure、artifact 狀態，支援 Evidence Explorer 與下載。

## 非目標

- 不保證價格方向，不提供交易、槓桿或資產配置指令，不執行資金異動。
- 不把第三方完整投資報告、社群意見或 LLM 內部推理視為已驗證 Evidence。
- 第一版不訓練或託管另一個通用聊天模型。

## 決策入口

Blocking OQ-B001..B015 與 nonblocking OQ-N001..N005 的 owner、deadline、proposed default、ADR 與狀態只在 `docs/architecture/open-questions.md` 維護；本文件不建立第二份 Open Question authority。
