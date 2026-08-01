# CryptoTrust Agent

## 加密市場多源資訊信任提煉系統 Master Spec

| 項目 | 內容 |
|---|---|
| 文件版本 | 0.5 |
| 文件狀態 | Kiro 實作基準草案 |
| 專案類型 | 加密市場分析 AI Agent |
| 指定資產 | BTC、ETH、SOL、BNB、XRP |
| 正式執行上限 | 900 秒 |
| 核心 AWS 服務 | Amazon Bedrock、Amazon Bedrock AgentCore、Amazon SageMaker AI |
| 開發環境 | Kiro |

> 本文件是產品目標、AWS 目標架構、模型分工、資料契約及驗收條件的整合規格。Kiro 應先讀取本文件，再參照 `.kiro/steering/`、`.kiro/specs/core-platform/`、`.kiro/specs/provider-adapters/` 與 `docs/architecture/` 的細部契約。若既有程式與本文件的 AWS 模型分工衝突，應先更新 requirements/design/tasks/schema，經確認後再修改程式。
>
> **來源信心通知**：Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 `SOURCE_TEXT_UNCERTAIN`。可靠性逐項登錄於 `docs/architecture/source-confidence-register.md`；blocking decision 只在 `docs/architecture/open-questions.md` 與對應 Proposed ADR 維護。任何 Proposed ADR 或已通過的 schema validation 都不等同人工 Approved。

---

## 1. 專案摘要

CryptoTrust Agent 是一套針對加密貨幣市場設計的多源資訊分析 AI Agent。系統接受指定幣種與分析題目後，必須在 900 秒內自動蒐集價格、新聞、官方公告、鏈上、社群與總體經濟資料，經過來源去重、可信度評估、量化分析及交叉驗證後，產出可回溯的市場分析報告。

系統不以單篇新聞摘要或單一技術指標作為主要結論，而是將輸出分成「事實、推論、結論」三個層級，並說明：

1. 哪些證據支持主要判斷。
2. 哪些證據反對主要判斷。
3. 不同資料來源是否一致。
4. 目前資料有哪些限制。
5. 哪些條件可能推翻目前結論。

每次正式執行必須產生：

- Final Report：市場分析報告。
- Evidence List：可回溯證據清單。
- Execution Log：Agent 與工具執行紀錄。
- Artifact Manifest：產出檔案、版本、格式與 SHA-256。

---

## 2. 問題與產品價值

### 2.1 使用者問題

加密市場全年無休，資訊同時分散於價格平台、新聞媒體、專案公告、監管機關、鏈上工具及社群平台。使用者通常面臨以下問題：

- 同一事件被大量轉載，造成虛假的多來源印象。
- 新聞標題與實際內文不一致。
- 社群聲量高，但來源可能不可靠。
- 價格、鏈上、新聞及社群訊號互相矛盾。
- 一般 AI 摘要缺少網址、取得時間及引用片段。
- 市場報告只給結論，沒有交代限制與反方證據。

### 2.2 產品價值

CryptoTrust Agent 將分散資訊轉換成可閱讀、可驗證、可回溯且具限制意識的分析結果。系統的價值不是代替使用者做投資決策，而是降低資料蒐集成本、提高來源透明度，並協助使用者理解市場判斷形成的原因。

---

## 3. 專案目標與非目標

### 3.1 核心目標

1. 支援 BTC、ETH、SOL、BNB、XRP。
2. 支援單一資產市場分析、假設驗證及雙資產比較題型。
3. 在 900 秒內完成蒐集、分析、推理、驗證及產出。
4. 同時使用市場、新聞、官方、鏈上、社群及總體資料；若資料無法取得，必須明確記錄 skipped 或 failed。
5. 每個重要事實與結論都能回溯至 Evidence 或 Analysis Result。
6. 保留反方證據與矛盾訊號，不因不利於主要結論而刪除。
7. 使用確定性程式或 SageMaker 模型處理數值分析，不讓 LLM 自行猜測市場數值。
8. 提供可供評審抽查的 Evidence List 與 Execution Log。

### 3.2 非目標

- 不保證價格漲跌。
- 不提供自動買進、賣出、槓桿或資產配置指令。
- 不執行交易或資金異動。
- 不將第三方完整投資報告直接當作系統最終判斷。
- 不將社群意見視為已驗證事實。
- 不將 LLM 內部推理文字當作證據。
- 第一版不使用 SageMaker 訓練或託管另一個通用聊天模型。

---

## 4. 官方資料集契約

### 4.1 資料包內容

主辦方資料包提供五個 CSV：

```text
data/hoya_bit/data/
├── BTC_daily_ohlcv.csv
├── ETH_daily_ohlcv.csv
├── SOL_daily_ohlcv.csv
├── BNB_daily_ohlcv.csv
└── XRP_daily_ohlcv.csv
```

### 4.2 已驗證特性

| 項目 | 規格 |
|---|---|
| 資料期間 | 2021-06-01 至 2026-05-31 |
| 時區 | UTC |
| K 線週期 | 1 日 |
| 報價單位 | USDT |
| 每個資產筆數 | 1,826 |
| 總筆數 | 9,130 |
| 欄位 | `date, open, high, low, close, volume` |
| Volume 語意 | Base asset interval volume |
| 缺值檢查 | 目前未發現缺值 |
| 重複日期檢查 | 目前未發現重複日期 |
| 基本 OHLCV 邏輯檢查 | 目前未發現異常 |

### 4.3 使用規則

1. 官方 CSV 是歷史市場分析的共同基準，不得以外部資料覆寫。
2. Live Market Source Extension 是主要路徑，而非例外分支。由於官方資料集固定截止於 2026-05-31，任何 reporting range 涵蓋該日期之後的查詢都必須啟用 live extension，並符合下列契約：
   - Live source 提供者必須於 `docs/specs/SPEC-0X` 指定，且支援與官方 CSV 相同的 `date, open, high, low, close, volume` 欄位、USDT 報價單位及 UTC 時區。
   - 所有數值一律使用 Decimal 解析，並保留至官方資料集相同的小數位數，不得因 API 回傳浮點數而降低精度。
   - Final Report 第 4 節與 Evidence List 必須逐筆標示資料點來自 `official_dataset` 或 `live_extension`，並在報告摘要揭露 `transition_date`（2026-05-31）。
   - Live source 不可用時，缺少資料的區間必須標記為 limitation，不得靜默重複填入官方資料集最後一筆資料。
3. 所有日期計算統一使用 UTC。
4. 不得直接比較不同資產的 base-asset volume 絕對值。
5. 指標計算需區分 warm-up range 與 reporting range。
6. 若 metadata、CSV 欄位、日期連續性或 OHLCV 約束失敗，readiness 或正式執行必須失敗，不得靜默改用其他資料。
7. 新聞、官方公告、鏈上、社群與總體資料不在此資料包內，必須由 Collector 額外取得。

### 4.4 Readiness 驗證

系統啟動時至少檢查：

- `dataset_metadata.json` 存在且可解析。
- 五個資產檔案均存在。
- CSV 欄位順序與名稱符合契約。
- 筆數、開始日期及結束日期符合 metadata。
- 日期不重複且按時間排序。
- `high >= open/close/low`。
- `low <= open/close/high`。
- `volume >= 0`。
- 所有數值均可使用 Decimal 安全解析。
- reporting range 涵蓋 2026-05-31 之後時，live source 憑證存在且可讀取。
- live source 可連線，且回傳 Schema、USDT 報價單位、UTC 時區與數值精度符合契約。

---

## 5. 使用情境

### 5.1 市場狀態分析

```text
請分析 BTC 過去兩週的市場表現，整合價格、新聞、鏈上活動及社群討論，判斷目前市場狀態。
```

### 5.2 假設驗證

```text
市場認為 ETH 短期內將維持盤整。請蒐集支持與反對證據，並提出最終判斷。
```

### 5.3 雙資產比較

```text
比較 SOL 與 BNB 在目前市場環境下的市場位置、流動性與風險特徵。
```

---

## 6. 端對端流程

```text
輸入驗證
→ 建立 Task
→ deterministic Planner 進行題型辨識並產生 Sourcing Plan / Analysis Plan
→ Formal Run Pre-flight Readiness Check
→ Pre-flight 通過後建立 Execution
→ 官方資料集與外部來源平行蒐集
→ Raw Record 保存
→ Nova 2 Lite 結構化抽取
→ Evidence 正規化與不可變保存
→ EvidenceClaimLink 建立 Claim 與 Evidence 關聯
→ EvidenceAssessment 計算、版本化與獨立來源聚類
→ 確定性市場指標與 SageMaker 推論
→ Trust 與 Contradiction 計算
→ Claude Opus 4.8 最終推理
→ 引用、數值、Schema 與信心驗證
→ Final Report、Evidence List、Execution Log、Manifest
```

---

## 7. 模型與服務分工

### 7.1 Amazon Nova 2 Lite

Nova 2 Lite 是大量新聞與文件處理模型，負責：

- 新聞正文結構化。
- 事件與幣種分類。
- 已確認事實與引用片段抽取。
- 初步情緒與相關性判斷。
- 新聞事件聚類與重複內容輔助判斷。
- 將內容轉為 Schema-constrained JSON。

Nova 2 Lite 不得決定來源需求分類、略過規劃步驟或直接產生最終市場結論。Planner 與 Web Grounding 的分工邊界如下：

- Planner（deterministic）決定查詢的來源類別、查詢字串與每個類別的時間預算，並將這些固定輸出寫入 Sourcing Plan，不受 Nova 影響。
- Web Grounding 只能在 Planner 核准的查詢字串與類別範圍內搜尋；回傳的具體 URL 是發現結果，不是規劃決策。
- 每個發現 URL 必須通過 Collector 的 allowlist、DNS/IP 驗證及 SSRF 防護，才可成為有效 Raw Source。
- Required 或 required-if-available 類別查無合格結果時，仍須依第 16 節的缺席語意處理，不得由 Nova 或 Web Grounding 靜默降級為 optional。
- 找到 URL 後仍須由自有 Collector 抓取及保存內容，以滿足 `fetched_at`、原始快照及引用抽查需求。
- 每次 Web Grounding 呼叫都必須在 Execution Log 記錄查詢字串、回傳 URL 數量，以及各 URL 通過或未通過 allowlist 的結果。

### 7.2 Amazon SageMaker AI

SageMaker 負責可重現的量化市場訊號。第一版建議使用 XGBoost Market Regime Classifier，並採 Serverless Inference 依請求計費。單次呼叫含冷啟動的初始逾時上限為 25 秒，並納入第 14 節時間預算；Phase D 完成後，必須依正式實測冷啟動延遲校準此數值、寫回第 14 與第 16 節，再評估是否改用 Provisioned Concurrency 或 Real-time Endpoint。

輸入特徵候選：

- 1 日、7 日、14 日及 30 日報酬率。
- Rolling volatility。
- 最大回撤。
- SMA 交叉與價格距離。
- 成交量變化率與 z-score。
- 可取得且時間對齊的鏈上聚合特徵。
- 經驗證的新聞情緒聚合值。

輸出契約：

```json
{
  "asset": "BTC",
  "as_of": "2026-08-01T00:00:00Z",
  "model_name": "market-regime-xgboost",
  "model_version": "1.0.0",
  "bullish_probability": 0.42,
  "bearish_probability": 0.21,
  "sideways_probability": 0.37,
  "anomaly_score": 0.18,
  "feature_window": {
    "start": "2026-07-03",
    "end": "2026-08-01"
  }
}
```

模型需採時間序列 walk-forward 驗證，不得隨機打散未來資料到訓練集。SageMaker 輸出是 Evidence/Analysis 的一部分，不是最終投資建議。

### 7.3 Claude Opus 4.8

Opus 4.8 是最終分析模型，僅接收已驗證且有上限的 Reasoning Context，負責：

- 整合高相關 Evidence 與 Analysis Result。
- 比較支持及反對證據。
- 判讀價格、新聞、鏈上與社群訊號的矛盾。
- 產生事實、推論、結論、限制及 watchpoints。
- 回答指定題目，不偏離成一般市場介紹。

Opus 不得直接存取網路、資料庫、AWS Secret 或原始 HTML。所有引用 ID 必須由程式驗證。

### 7.4 降級策略

| 階段 | 主要方案 | 降級方案 |
|---|---|---|
| 新聞抽取 | Nova 2 Lite | Schema 失敗時最多 repair 一次且上限 20 秒；仍失敗則 quarantine |
| 量化分析 | SageMaker Serverless Inference | 逾時或失敗時直接使用版本化確定性指標並揭露模型缺席，不重試 |
| 最終推理 | Claude Opus 4.8 | Claude Sonnet 5 |
| Web Grounding | Nova Web Grounding | 已核准的搜尋／新聞 API |

---

## 8. AWS 目標架構

### 8.1 邏輯架構

```text
使用者
  ↓
React / Amplify Hosting
  ↓
Amazon Cognito
  ↓
API Gateway
  ↓
Lambda BFF
  ↓
Deterministic Planner（產生固定的 Sourcing Plan / Analysis Plan）
  ↓
Step Functions（確定性 Orchestrator，由 Planner 輸出驅動）
  ├── SQS 平行工作
  ├── Lambda 靜態來源 Collector
  ├── ECS Fargate + Playwright 動態網頁 Collector
  ├── S3 Raw/Evidence/Artifact
  ├── DynamoDB Task/Evidence/Event 索引
  ├── SageMaker Serverless Inference（Market Regime 推論）
  └── Bedrock AgentCore Runtime（僅作為兩個受控步驟被呼叫，不決定流程分支）
       ├── Nova 2 Lite Extraction
       │    輸入：Raw Record
       │    輸出：Schema-constrained JSON；驗證失敗依第 16 節 quarantine
       └── Claude Opus 4.8 Reasoning
            輸入：已驗證且有上限的 Reasoning Context
            輸出：Facts / Inferences / Conclusions；失敗依第 16 節 repair 或 fallback

Bedrock Guardrails：套用於 Nova 與 Opus 兩個受控步驟的輸入／輸出，
而非授權 Agent 自由決定整體流程。

CloudWatch：Logs、Metrics、Trace、Alarm
Secrets Manager：外部 API 憑證
CloudTrail：AWS 操作稽核
```

### 8.2 責任邊界

| 元件 | 責任 |
|---|---|
| React | 題目輸入、進度、報告、Evidence Explorer |
| Cognito | 登入、JWT、角色與可信 `user_id`；`user_id` 只能由驗證後的 Token Claim 取得，不接受客戶端指定 |
| API Gateway / BFF | 驗證、任務 API、request fingerprint、冪等查找、額度判斷、回應整形與獨立限流；Task 建立每位使用者每小時最多 10 次，Pre-flight 每個 task 每分鐘最多 3 次 |
| Deterministic Planner | 依題型產生固定 Sourcing Plan、Analysis Plan 與來源需求矩陣，不呼叫 LLM |
| AgentCore Runtime | 執行受控的 Nova 抽取與 Opus 最終推理步驟，不得決定來源、略過步驟或流程分支 |
| Step Functions | 依 Planner 輸出執行可重試、可觀測且具 deadline 的確定性資料流程 |
| Lambda Collector | API、RSS、靜態 HTML |
| ECS Playwright | JavaScript 動態頁面 |
| S3 | 原始內容、報告、Evidence、Manifest |
| DynamoDB | Task、Execution、Evidence Metadata、Event Index |
| SageMaker | Market regime 與異常分數；第一版採 Serverless Inference，Phase D 後依實測延遲評估 Provisioned Concurrency 或 Real-time Endpoint |
| CloudWatch | 執行紀錄、效能與告警 |

Domain/Application 不得直接依賴 Boto3、FastAPI、資料庫 SDK 或模型 SDK。AWS 與模型呼叫都必須由 Infrastructure Adapter 實作。

---

## 9. 功能需求

### FR-001 任務建立

- 接受 question、assets、timeframe 及 formal mode。
- `user_id` 必須從已驗證的 Cognito JWT Claim 取得，不得接受 Request Body、Query String 或 Header 自訂值覆寫。
- 輸入驗證通過後，系統依 question、assets、timeframe 的正規化結果計算 `request_fingerprint`。正規化及雜湊規則必須版本化並於 `docs/specs/SPEC-0X` 明確定義，包括 question 前後空白處理、assets 大小寫與排序、timeframe 統一 UTC 並精確至分鐘等規則。
- 同一 `user_id` 在 24 小時內提交相同 `request_fingerprint` 時，Task 建立採 idempotency：回傳既有 `task_id`，不得建立新 Task。
- 未命中 idempotency 且輸入合法時建立唯一 `task_id`；開始執行時才建立 `execution_id`。
- `POST /api/v1/tasks` 採使用者層級獨立限流，每個 `user_id` 每小時最多 10 次請求，包含被 idempotency 導回既有 Task 的請求；超限回傳 429，且不得建立 Task。
- Formal Run 的 `execution_id` 必須在 Pre-flight Readiness Check 通過後建立。
- 非法幣種、模糊時間或不支援題型回傳 422，不建立任務。

### FR-002 題型與計畫

- 支援 market status、hypothesis validation、asset comparison。
- 從題目建立 answer dimensions、source categories、warm-up range 及 reporting range。
- 官方資料集永遠是 required source。
- Analysis Plan 必須包含 `source_requirement_matrix`，並至少符合下列基準：

```json
{
  "market status": {
    "market": "required",
    "news": "required",
    "official": "required",
    "on_chain": "optional",
    "social": "optional",
    "macro": "optional"
  },
  "hypothesis validation": {
    "market": "required",
    "news": "required",
    "official": "required",
    "on_chain": "required_if_available",
    "social": "optional",
    "macro": "optional"
  },
  "asset comparison": {
    "market": "required",
    "news": "required",
    "official": "required",
    "on_chain": "optional",
    "social": "optional",
    "macro": "optional"
  }
}
```

- `required`：缺席時將 execution 標記為 partial，並在 Final Report 限制章節強制揭露。
- `required_if_available`：存在 Collector 且未超出時間預算時必須嘗試；缺席只記錄 limitation，不影響 partial 狀態。
- `optional`：時間不足時可直接標記 skipped，不必嘗試。

### FR-003 多源資料蒐集

- 支援 market、news、official、on-chain、social、macro。
- 每個列入 Sourcing Plan 的來源都必須有 success、skipped 或 failed 結果；不得由 LLM 臨場移除。
- 記錄安全化參數、開始／結束時間、重試次數、來源 locator 與錯誤。

### FR-004 網頁抓取

- 優先使用 RSS、官方 API 或靜態 HTTP。
- 只有 JavaScript 動態來源才使用 Playwright。
- 保存 URL、canonical URL、HTTP status、published_at、fetched_at、正文、content hash 與原始 S3 URI。
- 遵循來源條款、robots 規則、速率限制與允許網域政策。

### FR-005 Evidence Pipeline

- Evidence 不可變且有 schema/version。
- Evidence 與 Task Claim 使用 link entity 關聯。
- EvidenceClaimLink 支援 support、counter、context 三種 stance，並允許同一 Evidence 關聯多個 Claim。
- Trust、relevance、independence、freshness 等可變評分不得寫入 Evidence，必須寫入獨立且版本化的 EvidenceAssessment。
- 同一 `evidence_id` 可對應多筆 EvidenceAssessment；報告與 Reasoning Context 一律引用最新一筆 assessment，並在 Execution Log 記錄使用的 `assessment_version`。
- 隔離缺少 lineage、跨 Task、引用不存在或 quarantined 的 Evidence。

### FR-006 去重與獨立來源

- 使用 canonical URL、content hash、標題相似度、事件主體及時間聚類。
- 相同通訊社稿件的轉載歸入同一 independence group。
- 重複內容不得提高 corroboration count。
- 維護版本化的 press-agency allowlist，包含 Reuters、Bloomberg、AP、CoinDesk wire 等已知通訊社與聚合來源網域。
- 內文 SimHash／MinHash 相似度超過規格門檻，且發布時間差位於同一事件聚類窗口時，歸入同一 `independence_group`；group 代表來源以 allowlist 中最早發布者為準。
- Allowlist 與相似度規則版本必須記錄於 `EvidenceAssessment.computation_ruleset_version`，以保留判斷依據。

### FR-007 市場分析

- 確定性計算 return、high/low、volume change、volatility、drawdown、SMA、trend、regime。
- 所有公式有版本與單元測試。
- 量化輸出必須帶來源期間、資料品質及計算版本。

### FR-008 Trust 與 Contradiction

- 分開計算 source trust、evidence relevance、freshness、independence、consistency。
- 偵測 numeric、temporal、source、narrative、signal、status conflict。
- 矛盾必須保留並影響 final confidence。

### FR-009 Structured Reasoning

- 事實必須引用 Evidence 或 Analysis ID。
- 推論必須引用事實。
- 結論必須引用事實或推論。
- Opus 輸出失敗時只允許一次 repair，固定上限為 60 秒；repair 失敗或逾時後直接切換 Sonnet 5，不得對 Opus 進行第二次重試。
- Sonnet 5 fallback 輸出仍無法通過驗證時安全結束，不得寫入未驗證報告。

### FR-010 報告與競賽產出

- 產出 Final Report JSON、Markdown 及 HTML。
- 產出 Evidence List JSON、CSV。
- 產出 Execution Log JSONL。
- 產出包含 SHA-256、MIME type、schema version、generated_at 的 manifest。

### FR-011 Formal Run

- 使用者提交正式執行前，系統先執行 Pre-flight Readiness Check，且不消耗正式執行額度：
  1. 驗證資產代碼、時間範圍及題型合法性。
  2. 驗證官方資料集 readiness。
  3. 對 Bedrock Nova／Opus、SageMaker 推論服務及外部來源 allowlist API 執行輕量健康探測，不執行完整推論。
  4. 任一必要依賴不健康時，UI 顯示「目前不建議提交正式執行」及具體原因；使用者可稍後重試，不消耗正式額度。
- Pre-flight 採獨立限流，每個 task 每分鐘最多 3 次；超限請求不得觸發外部依賴健康探測。
- Pre-flight 通過後才鎖定 question、assets、timeframe，並建立正式 `execution_id`。
- 一般使用者在同一 task 下只允許一次正式執行；Pre-flight 不計入此限制。
- 正式執行額度以 `(user_id, request_fingerprint)` 為控管單位，且獨立於 24 小時 Task idempotency 視窗保存；建立新的 `task_id` 不得重置相同額度範圍的執行次數。
- 技術故障重跑必須由管理者授權並保留第一次紀錄；Execution Log 必須記錄 `original_execution_id`。
- 同一 `(user_id, request_fingerprint)` 最多允許管理者授權一次技術性重跑，正式 Execution 總數上限為 2 次：使用者原始執行一次，加上管理者授權重跑一次。
- 管理者授權後的重跑再次故障時，必須轉入人工個案處理，不得透過 Formal Run 流程再次自動重跑。
- 管理者授權重跑必須在 Execution Log 記錄具體 `technical_failure_code`，例如 `sagemaker_endpoint_unavailable`、`bedrock_throttled` 或 `infra_timeout`。
- UI 顯示進度、剩餘時間、部分失敗及 artifact 狀態。

---

## 10. Evidence Schema

Evidence、Claim 關聯與可變評分必須分離保存，避免重新分群或重算 Trust 時修改原始證據。

### 10.1 Evidence（不可變）

Evidence 在抓取與正規化完成後即固定，不保存任何會隨後續證據變動的評分或 Claim 關聯：

```json
{
  "schema_version": "1.0.0",
  "evidence_id": "EV-20260801-001",
  "task_id": "TASK-001",
  "source_name": "Example Source",
  "source_url": "https://example.com/article",
  "canonical_url": "https://example.com/article",
  "source_type": "official",
  "published_at": "2026-08-01T01:00:00Z",
  "fetched_at": "2026-08-01T02:10:30Z",
  "content_reference": "可在原始內容找到的引用片段或指標值",
  "related_assets": ["BTC"],
  "raw_content_s3_uri": "s3://bucket/raw/example.html",
  "content_hash": "sha256-value",
  "query_provenance": {
    "collector": "official_news_collector",
    "query": "BTC regulatory announcement",
    "parameters": {}
  }
}
```

### 10.2 EvidenceClaimLink（多對多）

```json
{
  "link_id": "LINK-000123",
  "evidence_id": "EV-20260801-001",
  "task_id": "TASK-001",
  "claim_id": "CLAIM-002",
  "stance": "support",
  "created_at": "2026-08-01T02:15:00Z"
}
```

`stance` 只允許 `support`、`counter` 或 `context`。Evidence 與 Claim 可多對多關聯，且 link entity 不得修改 Evidence 本體。

### 10.3 EvidenceAssessment（可變且版本化）

```json
{
  "assessment_id": "ASSESS-000045",
  "evidence_id": "EV-20260801-001",
  "assessment_version": "1.0.0",
  "computed_at": "2026-08-01T02:16:00Z",
  "source_trust_score": 0.9,
  "evidence_relevance_score": 0.86,
  "independence_group": "EVENT-001",
  "freshness_score": 0.95,
  "computation_ruleset_version": "trust-rules-1.0.0"
}
```

同一 `evidence_id` 可建立多筆 EvidenceAssessment，以 `computed_at` 與 `assessment_version` 保留重算歷史。Final Report 與 Reasoning Context 使用最新一筆 Assessment，Execution Log 必須記錄實際使用的 `assessment_id` 與 `assessment_version`。

所有必要欄位不得由 LLM 虛構。`fetched_at`、hash、S3 URI、HTTP metadata、工具執行資訊、link ID 與 assessment 版本資訊必須由程式產生。

---

## 11. Final Report 結構

1. 執行摘要。
2. 對指定題目的直接回答。
3. 市場判斷：偏多、偏空、盤整、訊號混合或資料不足。
4. 已確認事實。
5. 關鍵推論。
6. 支持證據。
7. 反方證據。
8. 價格、鏈上、新聞、社群及總體訊號一致性。
9. Contradictions。
10. Final confidence 與組成。
11. 限制、風險及資料不足。
12. 可能推翻結論的條件。
13. 後續觀察重點。
14. 免責聲明：本內容為資訊分析，不構成投資建議。

若 reporting range 涵蓋 2026-05-31 之後，執行摘要必須揭露 `transition_date`；第 4 節已確認事實與 Evidence List 必須標示每筆市場資料來自 `official_dataset` 或 `live_extension`。所有引用 Evidence 評分的段落必須可回溯至實際使用的 `assessment_id` 與 `assessment_version`。

---

## 12. Execution Log

每一行為一筆 JSONL event，至少包含：

```json
{
  "timestamp": "2026-08-01T02:10:30.123Z",
  "task_id": "TASK-001",
  "execution_id": "EXEC-001",
  "step": "collect_news",
  "tool": "official_news_collector",
  "status": "completed",
  "duration_ms": 842,
  "retry_count": 0,
  "sanitized_parameters": {
    "asset": "BTC"
  },
  "result_summary": {
    "records": 12
  }
}
```

Log 不得包含 API Key、完整認證 Header、Cognito Token 或未遮罩個資。

凡 Reasoning Context 或 Final Report 使用 EvidenceAssessment，Execution Log 必須記錄對應的 `evidence_id`、`assessment_id` 與 `assessment_version`。管理者授權技術故障重跑時，新的 execution event 必須記錄 `original_execution_id`、`technical_failure_code` 與目前 `(user_id, request_fingerprint)` 額度範圍的正式執行次數，並保留原 execution 的完整紀錄。

Task 建立事件必須記錄 `request_fingerprint`、`fingerprint_ruleset_version`、`idempotency_outcome`（`created` 或 `reused`）、解析後的 `task_id` 與使用者層級限流結果。Log 僅能保存由 `user_id` 衍生的穩定假名識別碼，不得記錄 Cognito Token 或未遮罩的使用者身分資料。

每次 Web Grounding 呼叫至少記錄 Planner 核准的來源類別、查詢字串、回傳 URL 總數，以及每個 URL 的 allowlist 驗證結果與拒絕原因。這些欄位只記錄發現結果，不得改寫原 Sourcing Plan。

---

## 13. API 初步契約

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/v1/tasks` | 建立分析任務；同一使用者 24 小時內相同 question／assets／timeframe 的 `request_fingerprint` 回傳既有 `task_id`；每位使用者每小時最多 10 次請求，包含冪等命中，超限回傳 429 |
| POST | `/api/v1/tasks/{task_id}/preflight` | 執行不消耗正式額度的 Formal Run readiness 檢查；獨立限流，每個 task 每分鐘最多 3 次，避免無限探測外部依賴 |
| POST | `/api/v1/tasks/{task_id}/executions` | Pre-flight 通過後建立並啟動正式 Execution |
| GET | `/api/v1/tasks/{task_id}` | 查詢任務與執行狀態 |
| GET | `/api/v1/tasks/{task_id}/evidence` | 取得 Evidence List |
| GET | `/api/v1/tasks/{task_id}/artifacts` | 取得 Artifact Manifest |
| GET | `/api/v1/executions/{execution_id}/events` | 取得 Execution Log |
| GET | `/api/v1/reports/{report_id}` | 取得 Final Report |
| GET | `/api/v1/health` | 程式存活檢查 |
| GET | `/api/v1/ready` | 資料集與必要依賴 readiness |

建立任務範例：

```json
{
  "question": "分析 BTC 過去兩週的市場狀況",
  "assets": ["BTC"],
  "timeframe": {
    "start": "2026-07-18T00:00:00Z",
    "end": "2026-08-01T00:00:00Z"
  },
  "formal_run": true
}
```

`user_id` 由 BFF 從已驗證的 Cognito JWT 取得，不屬於 Request Body。`formal_run: true` 只代表使用者有正式執行意圖；建立 Task 本身不消耗正式額度，但仍計入每位使用者每小時 10 次的 Task 建立限流。客戶端仍須依序呼叫 Pre-flight 與 Execution API，且只有 Pre-flight 通過後才能建立正式 `execution_id`。

Task 建立命中 24 小時 idempotency 規則時回傳既有 `task_id`，並明確標示 reuse 結果；此請求仍計入 Task 建立限流。超過每位使用者每小時 10 次時回傳 429，不得建立新 Task，也不得改變 `(user_id, request_fingerprint)` 的正式執行額度。

Pre-flight 超過獨立限流時回傳 429，並提供可安全揭露的重試等待資訊；被限流的請求不消耗正式執行額度，也不得觸發外部健康探測。

---

## 14. 900 秒時間預算

900 秒是不可延長的 hard deadline。各階段同時具有正常情況的 Target 區間與不得越過的 Hard Deadline；可壓縮階段不得侵蝕最終推理、驗證與寫入時間。各階段 Target 結束至 Hard Deadline 的差值是該階段專屬緩衝，不同階段不得互相挪用；只有最終寫入階段可依本版時間表，預先由驗證階段切割固定額度。

| 階段 | Target 區間 | Hard Deadline | 可壓縮 | 說明 |
|---|---|---|---|---|
| 輸入驗證／建立 Task／題型分類 | 0:00-0:20 | 0:30 | 否 | 專屬緩衝 10 秒；純程式邏輯，不含外呼 |
| 建立 Sourcing Plan 與 Analysis Plan | 0:20-1:30 | 2:00 | 否 | 專屬緩衝 30 秒；Deterministic Planner，不呼叫 LLM |
| 平行蒐集（API／靜態 HTTP／動態 Playwright） | 1:30-6:00 | 7:30 | 是 | 專屬緩衝 90 秒，含 ECS 冷啟動；優先犧牲低優先來源 |
| Nova 抽取／Evidence 正規化／去重 | 3:00-8:00 | 8:30 | 是 | 專屬緩衝 30 秒；與蒐集 pipeline 化重疊，部分來源可 quarantine |
| 確定性分析與 SageMaker 推論 | 7:30-9:30 | 10:00 | 否 | 專屬緩衝 30 秒；SageMaker 單次呼叫含冷啟動逾時上限 25 秒，超過即使用確定性指標，保留 5 秒切換時間且不重試 |
| Trust／Contradiction／Reasoning Context | 9:30-11:00 | 11:30 | 否 | 專屬緩衝 30 秒；使用最新且已記錄版本的 EvidenceAssessment |
| Opus 最終推理（含一次 repair） | 11:00-13:00 | 13:30 | 否 | 專屬緩衝 30 秒；repair 固定上限 60 秒，逾時直接進入 Sonnet 5 fallback |
| 引用／Schema／數值／Artifact 驗證 | 13:30-14:20 | 14:35 | 否 | 專屬緩衝 15 秒；不接受未驗證模型輸出 |
| 最終寫入／Manifest／UI 更新 | 14:35-14:55 | 15:00（900 秒） | 否 | Target 20 秒、專屬緩衝 5 秒；必須保留最低合格 Artifact Bundle |

階段相依關係採 DAG；蒐集完成的 Raw Record 可立即進入 Nova，不必等待全部來源完成：

```text
輸入驗證
  → Deterministic Planner
    → 平行蒐集
      ├─→ Market Data ─→ 確定性分析／SageMaker ─┐
      └─→ Raw Records ─→ Nova／Evidence Pipeline ─┤
                                                   └─→ Trust／Contradiction／Reasoning Context
                                                       → Opus／Fallback
                                                       → 驗證
                                                       → 最終寫入
```

系統緩衝原則：

1. 各階段 Target 結束至 Hard Deadline 的差值就是該階段專屬緩衝，用途與秒數已在表格逐列標明；不存在可跨階段使用的籠統全域緩衝。
2. 最終寫入 Target 由 14:45-15:00 調整為 14:35-14:55，透過驗證階段 Target 收斂 10 秒取得，讓寫入具有 20 秒執行額度與 5 秒專屬緩衝；驗證邏輯不得因此刪減，只能優化執行效率。
3. 蒐集或抽取逾時時，優先犧牲低優先來源，不得侵蝕 Opus 推理、驗證及最終寫入額度。
4. 每個有界重試都必須設定固定秒數上限；Nova repair 上限 20 秒，Opus repair 上限 60 秒，不得重試直到成功。
5. 低優先來源必須有明確 time budget。剩餘時間不足時停止新增低可信來源，優先完成必要來源、主要 Evidence、報告與 log。

最終寫入的 20 秒 Target 與 5 秒專屬緩衝是第一版估算值。Phase F Dress Rehearsal 必須以接近正式賽事規模的 Evidence 數量，實測 Final Report（JSON／Markdown／HTML）、Evidence List（JSON／CSV）、Execution Log（JSONL）、Manifest、逐檔 SHA-256 與 S3 寫入總耗時，並將校準結果寫回本節。若 20 秒不足，只能從驗證階段或其他仍有餘裕的專屬緩衝重新切割時間，不得放寬 900 秒總 deadline。

題型差異化時間分配：

- Market Status／Hypothesis Validation（單資產）：沿用上述標準時間表。
- Asset Comparison（雙資產）：
  - 兩資產的 Collector 必須在 1:30-6:00 Target 區間內透過 SQS 平行觸發，不得序列化執行。
  - 每個資產的低優先來源時間預算減半，優先確保兩資產都取得 market、news、official 三類必要來源。
  - 若 7:30 Hard Deadline 前仍無法完成兩資產的必要來源，優先完整覆蓋使用者指定的第一資產；第二資產標記為 partial，並在 Final Report 限制章節揭露。

---

## 15. 安全需求

1. 所有 AWS 權限採 least privilege。
2. 瀏覽器端不保存 AWS 或外部服務 Secret。
3. 憑證保存於 Secrets Manager。
4. 網頁內容一律視為不可信資料，不得覆蓋 system/developer instruction。
5. 移除 script、隱藏內容及可疑指令；保留清理前後 hash 與 lineage。
6. Collector 必須有 allowlist、DNS/IP 驗證、redirect 上限、payload 上限及 timeout，避免 SSRF。
7. Bedrock Guardrails 檢查使用者輸入與最終輸出。
8. 日誌與 Trace 不保存憑證或不必要的完整 Prompt。
9. 正式報告保留所有引用 URL 及來源聲明。
10. Formal Execution 與 Pre-flight 採獨立限流；Pre-flight 每個 task 每分鐘最多 3 次，超限請求不得繼續呼叫外部依賴。
11. Task 建立另採使用者層級限流與 24 小時 idempotency；可信 `user_id` 只能取自已驗證的 Cognito Token，正式執行額度必須綁定 `(user_id, request_fingerprint)`，不得因改用新 `task_id` 重置。

---

## 16. 錯誤與降級語意

| 情境 | 處理方式 |
|---|---|
| 官方資料集失敗 | Execution failed；不得替換資料 |
| Required source 缺席 | 繼續完成可用產出，Execution 標記 partial，並在限制章節強制揭露 |
| Required-if-available source 缺席 | 記錄 limitation；不因該來源單獨標記 partial |
| Optional source 失敗或時間不足 | 繼續執行，記錄 failed 或 skipped 與 limitation |
| Live extension 失敗 | 缺少區間標記 limitation，不得以前一筆資料填補；若因此缺少 required market coverage，標記 partial |
| 動態網頁失敗 | 在該 Collector 固定時間預算內有界重試／切換 Playwright；仍失敗則保留 failed event |
| Nova Schema 不合法 | 最多一次 repair，固定上限 20 秒；再失敗則 quarantine 該來源 |
| SageMaker 失敗或逾時（單次呼叫含冷啟動逾時上限 25 秒） | 不重試，直接使用確定性指標並揭露模型缺席 |
| Opus 失敗／限流 | 最多一次 repair，固定上限 60 秒；失敗或逾時後切換 Sonnet 5，不得再次重試 Opus |
| Sonnet 5 fallback 不合法 | 安全結束，不得寫入未驗證報告 |
| 引用不存在 | 拒絕模型輸出並納入同一次 60 秒 repair 額度，不得另開重試預算 |
| Artifact Renderer 失敗 | Canonical JSON 成功時標記 partial，保留可用產出 |
| 即將超時 | 停止低優先工作，完成最低合格 Artifact Bundle |

---

## 17. 驗收條件

### 17.1 資料集

- 五個檔案均通過 readiness。
- 每個資產讀取 1,826 筆或指定期間所需子集合。
- 輸出保留 UTC、USDT、來源檔案與行號 lineage。
- reporting range 涵蓋 2026-05-31 之後時，會加入 live source，且通過憑證、連線、Schema、精度、UTC 與 USDT 契約檢查。
- Final Report 與 Evidence List 會揭露 `transition_date`，並逐筆標示 `official_dataset` 或 `live_extension`。
- Live extension 缺少的資料區間不會使用官方資料集最後一筆靜默填補。

### 17.2 Evidence

- 每筆 Evidence 都有 source、fetched_at、content_reference、raw locator 與 content hash，且建立後不可修改。
- Evidence 與 Claim 透過 EvidenceClaimLink 多對多關聯，不在 Evidence 本體保存 `related_claim` 或 `stance`。
- Trust、relevance、freshness 與 independence 只存在版本化的 EvidenceAssessment；重算不會覆寫歷史版本。
- Final Report 與 Reasoning Context 使用的 `assessment_id`、`assessment_version` 可由 Execution Log 回溯。
- 網頁 Evidence 有 source_url、raw content locator 及 hash。
- 報告的重要事實均可在 Evidence/Analysis 找到。
- 重複轉載不增加獨立佐證數量。

### 17.3 推理

- Facts、Inferences、Conclusions 分層。
- Facts 引用有效 ID。
- 報告直接回答指定題目。
- 至少保留可取得的 counter-evidence；找不到時需明確說明搜尋範圍。
- Final confidence 可拆解並可重算。

### 17.4 執行

- 完整正式流程小於 900 秒。
- Formal Run 的 Pre-flight 不消耗正式執行額度；必要依賴不健康時不建立正式 `execution_id`。
- Pre-flight 每個 task 每分鐘最多 3 次；第 4 次請求回傳 429，且不呼叫外部健康探測。
- 同一使用者在 24 小時內以相同 question、assets、timeframe 重複呼叫 `POST /tasks` 時，回傳既有 `task_id`，並標示 idempotency reuse，不建立新 Task。
- `POST /tasks` 對單一使用者每小時最多接受 10 次請求；超限回傳 429，不建立 Task、不消耗正式執行額度，且冪等命中的請求仍計入限流。
- SageMaker 單次呼叫含冷啟動在 25 秒內完成；逾時時不重試，並正確切換確定性指標。
- 同一 `(user_id, request_fingerprint)` 的正式 Execution 總數最多 2 次；第 2 次必須由管理者授權並記錄 `original_execution_id` 與 `technical_failure_code`。即使 24 小時後建立新的 `task_id`，額度也不重置；再次故障後轉人工處理。
- 單一 Optional source 失敗不會造成全系統中止。
- 雙資產 Collector 會平行執行；超過蒐集 Hard Deadline 時依第一資產優先規則標記 partial。
- 產出 Final Report、Evidence List、Execution Log、Manifest。
- 所有 major step 有 timestamp、duration、outcome。

### 17.5 測試層級

- Unit：值物件、公式、狀態機、Schema、Trust、Confidence、request fingerprint 正規化與版本化雜湊。
- Contract：Collector、Repository、Reasoning Provider、SageMaker Adapter、Artifact Writer、Task idempotency 與額度 Repository。
- Integration：Dataset-to-Evidence、Bedrock、SageMaker、API/use case boundary。
- Architecture：禁止不合法的 dependency import。
- E2E：三種題型、完整成功、source requirement matrix、Task 24 小時 idempotency、每使用者 Task 限流、跨 task 額度不可重置、optional partial、Pre-flight 限流、SageMaker 25 秒逾時、技術重跑上限、deadline、雙資產 partial、降級與 citation audit。

---

## 18. 現有程式基線與目標差距

目前 repository 已具備：

- Python 分層架構。
- 官方資料集讀取與驗證。
- FastAPI 與 Streamlit 基礎介面。
- Planner、Evidence、Trust、Reasoning、Reporting 與 Artifact 基礎流程。
- Unit、Contract、Integration、Architecture、E2E 測試。
- 900 秒 deadline 與本機 Demo。

仍需由 Kiro 規劃及實作的 AWS 目標差距：

1. 將既有 Evidence 契約遷移為 Evidence、EvidenceClaimLink、EvidenceAssessment，並定義相容與資料遷移策略。
2. 強化 deterministic Planner，使其輸出固定來源類別、查詢字串、類別時間預算、Analysis Plan 與 source requirement matrix。
3. 新增 Formal Run Pre-flight Readiness Check、每 task 每分鐘 3 次的獨立限流、正式額度保護、單次管理者重跑上限與技術故障分類紀錄。
4. 將目前 OpenAI-compatible reasoning adapter 替換或並存為 Bedrock Opus 4.8 adapter。
5. 新增 Nova 2 Lite extraction adapter 與嚴格輸出 Schema。
6. 新增 Web Grounding／搜尋來源發現流程、URL 安全驗證與發現結果稽核，但不得改寫 Sourcing Plan 或取代 raw fetch。
7. 將 skipped news/official collector 改為正式 Collector。
8. 新增靜態 HTTP 與 Playwright 動態網頁 Collector。
9. 新增 SageMaker Market Regime adapter、model artifact、Serverless Inference 契約與初始 25 秒逾時設定。
10. 新增 AgentCore Runtime 受控步驟 integration，並確保 Nova／Opus 不具備流程分支或來源取捨權限。
11. 新增 AWS persistence、queue、artifact storage 與 observability adapters。
12. 更新 `.env.example`、deployment、demo runbook 與 AWS 架構文件。
13. 新增 Bedrock/SageMaker contract tests，以及 Pre-flight 限流、重跑上限與超時降級 E2E。
14. 為 Task 建立新增版本化的 `request_fingerprint` 正規化規則、24 小時 idempotency 查找與每使用者每小時 10 次限流，並將正式執行額度控管單位由單純 `task_id` 改為 `(user_id, request_fingerprint)`。
15. Phase F 使用接近正式賽事規模的 Artifact Bundle，實測報告、Evidence List、Execution Log、Manifest、SHA-256 與 S3 寫入耗時，並校準第 14 節最終寫入的 20 秒 Target；不得放寬 900 秒總 deadline。

---

## 19. Kiro 實作規則

Kiro 執行任何修改前必須：

1. 先讀取 `.kiro/steering/product.md`、`tech.md`、`structure.md`。
2. 讀取 `.kiro/specs/crypto-trust-agent/requirements.md`、`design.md`、`tasks.md`。
3. 讀取 `docs/specs/SPEC-01` 至 `SPEC-10` 中與任務相關的規格。
4. 先更新 requirements/design/tasks，不能直接改程式。
5. 每次只實作一個可驗收的垂直切片。
6. 不修改已通過的 Domain 契約，除非先說明 migration 與相容策略。
7. 新服務必須先定義 Port，再增加 Infrastructure Adapter。
8. 外部服務測試使用 mock/stub；正式 AWS integration test 與本機單元測試分離。
9. 不在原始碼、Prompt、測試 fixture 或 log 中寫入 Secret。
10. 每個階段完成後執行相關測試，更新 tasks checkbox 與驗證證據。
11. Agent（Nova／Opus）不得決定是否蒐集來源類別或略過規劃步驟。來源類別、查詢字串、類別時間預算與 Analysis Plan 一律由 deterministic Planner 產生，作為 Step Functions 的固定輸入；Nova／Opus 僅能在抽取與最終推理兩個受控語意步驟內產生內容，且輸出通過程式驗證後才可進入下一階段。
11-a. Web Grounding 是範圍內探索工具，其 URL 回傳結果不構成 Sourcing Plan 變更。找不到結果時，缺席語意必須依第 16 節的 required、required-if-available 或 optional 分類處理，不得由 Nova 或 Web Grounding 自行改變需求分類。

建議新增的 Kiro Phase：

```text
Phase A：Bedrock Provider Contracts
Phase B：News Discovery and Raw Fetch
Phase C：Nova Evidence Extraction
Phase D：SageMaker Market Regime
Phase E：AgentCore and AWS Adapters
Phase F：Formal-run Integration and Dress Rehearsal
```

不得因 AWS 目標架構而一次重寫整個既有程式。優先保留已驗證的 Domain、Dataset、Evidence、Trust、Reporting 與測試資產。

---

## 20. 競賽評分對應

| 評分方向 | 系統對應 |
|---|---|
| 創意度 | 獨立來源聚類、反方證據、跨訊號矛盾、可重算信心 |
| 技術可行性 | AgentCore、Step Functions、Nova、SageMaker、Opus、完整降級 |
| 商業應用性 | 降低資訊整理成本、來源透明、不代替投資決策 |
| 主題切合度 | 多源整合、證據回溯、信心校準、限制說明 |
| 完成度 | Web Demo、Final Report、Evidence List、Execution Log、Source/Config |
| Kiro 加分 | Kiro requirements/design/tasks/steering 與實作紀錄 |

---

## 21. 建議實作優先順序

### P0：不可缺少

- 官方資料集與 live source transition。
- 正式 News／Official Collector。
- Evidence lineage、引用驗證及 Artifact Bundle。
- Deterministic Planner、Sourcing Plan 與 source requirement matrix。
- Nova 結構化抽取。
- Opus 最終推理。
- Task idempotency、使用者層級建立限流，以及跨 task 不可重置的 `(user_id, request_fingerprint)` 正式執行額度。
- 900 秒 formal-run E2E。

### P1：競爭力功能

- SageMaker Market Regime。
- SageMaker 部署模式決策：Phase D 必須附上 Serverless Inference 實測冷啟動延遲，校準目前 25 秒逾時上限並寫回第 14 與第 16 節，再作為是否改用 Provisioned Concurrency 或 Real-time Endpoint 的依據。
- 獨立來源聚類。
- Web Grounding 與 deterministic Planner 權限邊界及發現結果稽核。
- Formal Run 管理者重跑上限與技術故障分類。
- 完整 Contradiction taxonomy。
- Evidence Explorer。
- AgentCore/AWS Demo 部署。

### P2：時間足夠再做

- 多 Agent debate。
- 進階鏈上與社群來源。
- Pre-flight 獨立限流與 API Gateway 部署設定。
- Phase F Artifact Bundle 寫入與 SHA-256 耗時實測，校準最終寫入的 20 秒 Target。
- PDF 品牌化輸出。
- 完整 production IaC 與多區域容錯。

---

## 22. 完成定義

專案只有在以下條件全部成立時才視為完成：

- 可輸入正式題目與指定資產。
- Task 建立使用 Cognito `user_id` 與版本化 `request_fingerprint`；24 小時內的相同請求回傳既有 `task_id`，每位使用者每小時最多呼叫 10 次。
- 正式執行前會完成不消耗額度的 Pre-flight；只有通過後才建立正式 Execution，且每個 task 每分鐘最多呼叫 Pre-flight 3 次。
- 同一 `(user_id, request_fingerprint)` 的正式 Execution 總數不超過 2 次，且不因建立新 `task_id` 重置；管理者重跑具有原始執行關聯與技術故障分類，再次故障會轉人工處理。
- 在 900 秒內完成單次正式分析。
- Phase F 已以接近正式規模的 Artifact Bundle 實測並校準最終寫入 Target；任何調整均未放寬 900 秒總 deadline。
- SageMaker 單次呼叫具有明確的 25 秒逾時上限，失敗時不重試並切換確定性指標。
- Web Grounding 只在 Planner 核准範圍內發現 URL，且查詢、回傳數量及 allowlist 結果可由 Execution Log 稽核。
- 使用官方 OHLCV 基準與必要的 live extension。
- 市場、新聞與官方三類 required 來源缺席時明確標記 partial；required-if-available 與 optional 來源依矩陣記錄 limitation、skipped 或 failed。
- Evidence、EvidenceClaimLink 與 EvidenceAssessment 已分離，且評分重算歷史可回溯。
- 報告中的重要結論可回溯至 Evidence/Analysis ID。
- 正反證據、矛盾、信心、限制與 watchpoints 均可顯示。
- Final Report、Evidence List、Execution Log 與 Manifest 可下載。
- API、UI、降級與引用稽核測試通過。
- README、AWS 架構、部署說明與 Demo Runbook 已更新。
- Kiro requirements/design/tasks 與實際程式狀態一致。

---

## 23. 官方技術參考

- [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
- [Amazon Nova 2 Lite](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html)
- [Amazon Nova Web Grounding](https://docs.aws.amazon.com/nova/latest/nova2-userguide/web-grounding.html)
- [Claude Opus 4.8 on Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-8.html)
- [Amazon SageMaker Serverless Inference](https://docs.aws.amazon.com/sagemaker/latest/dg/serverless-endpoints.html)
- [Amazon SageMaker Real-time Inference](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints.html)
- [Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-how.html)
