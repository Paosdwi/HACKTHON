# Architecture Open Questions

## Source policy

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。

ADR-001～ADR-010 與 OQ-B001～OQ-B011 已由 Maintainer 於 2026-08-01 人工核准。OQ-B012～OQ-B015 仍依各自 production implementation gate 決議；Nonblocking 項目維持 `Before production deployment`。

## Blocking

| ID | description | source | blocking yes-no | blocks | owner | proposed default | decision deadline | resolution ADR | status |
|---|---|---|---|---|---|---|---|---|---|
| OQ-B001 | **Fingerprint canonicalization**：Unicode、空白、asset order、time precision、hash 與 ruleset是否核准 | Master FR-001；ADR-001 design inference | yes | Task identity、24h idempotency、quota scope | Core Maintainer + Product Owner | 採 ADR-001 的 `fingerprint-1.0.0` | Approved 2026-08-01 | ADR-001 | approved |
| OQ-B002 | **Identity claims**：Cognito subject/admin claim、authorization 與 log pseudonym key rotation 是否核准 | Master FR-001/011；ADR-002 design inference | yes | identity boundary、tenant scope、admin rerun audit | Security Owner + Core Maintainer | 採 ADR-002 | Approved 2026-08-01 | ADR-002 | approved |
| OQ-B003 | **Distributed deadline**：DeadlineDTO wire 欄位、跨 process propagation、receiver-local monotonic enforcement 與 safety margin 是否核准 | Master 900 秒/monotonic requirement；current user distributed-deadline principles；ADR-003 inferred safety bounds | yes | 所有跨 process Port、provider timeout、outer guards | Core Maintainer + Provider Owners | 採 ADR-003，包含 `1000/100/5000 ms` safety default/bounds | Approved 2026-08-01 | ADR-003 | approved |
| OQ-B004 | **完整 Port DTO schemas**：十個 Port 的完整 method signatures、DTO、errors、pagination/consistency 與 shared contract suite 是否凍結 | CP-PORT-*；`docs/architecture/ports-and-schemas.md` | yes | 所有 Core/Provider 並行開發與 adapter conformance | Core Maintainer | 11 schemas/37 methods/37 valid/37 invalid；移除 `TaskRepository.lock_for_execution`/`CT-TASK-LOCK-EXECUTION-01` | Approved 2026-08-01 | schema review/ADR-010 | approved |
| OQ-B005 | **Decimal wire format**：decimal string grammar、38/18 precision/scale、domain ranges、sum tolerance 與 compatibility adapter 是否核准 | Master FR-007 Decimal-safe obligation；ADR-004 design inference | yes | Port DTO numeric compatibility、canonical hashing、market analysis | Core Maintainer + Market Data Provider Owner | 採 ADR-004 | Approved 2026-08-01 | ADR-004 | approved |
| OQ-B006 | **Evidence locator**：`raw_locator`、ContentReferenceDTO、lineage/latest/pagination/idempotency 細節是否核准 | Master FR-005/JSON；ADR-005 design inference | yes | Evidence schema、repository semantics、citation lineage | Core Maintainer + Persistence Provider Owner | 採 ADR-005 | Approved 2026-08-01 | ADR-005 | approved |
| OQ-B007 | **Pre-flight validity**：pass TTL、input/dependency binding、single-use 與 Execution create revalidation 是否核准 | Master FR-011；ADR-006 design inference | yes | Formal Execution eligibility、quota safety | Core Maintainer | 採 ADR-006 的 60 秒與 atomic consume | Approved 2026-08-01 | ADR-006 | approved |
| OQ-B008 | **Collector security limits**：providers、domain allowlist/robots 行為及精確安全限制是否核准 | Master FR-003/004/015；ADR-007 design inference | yes | SourceCollector contract、安全測試、Web Grounding fetch | Collector Provider Owner + Security Owner | 採 ADR-007 的安全限制 | Approved 2026-08-01 | ADR-007 | approved |
| OQ-B009 | **Reasoning context bounds**：Structured Reasoning Context 的 bytes/tokens/records/excerpt/question bounds 是否核准 | Master FR-009；ADR-008 design inference | yes | ReasoningProvider contract、deterministic truncation、provider safety | Reasoning Provider Owner + Core Maintainer | 採 ADR-008 | Approved 2026-08-01 | ADR-008 | approved |
| OQ-B010 | **Minimum artifact bundle**：degraded minimum、Manifest、自我列舉與 publication gate 是否核准 | Master FR-010/016 normal formats；ADR-009 design inference | yes | report publication、partial semantics、ArtifactRepository contract | Artifact Owner + Core Maintainer | 採 ADR-009；Manifest 不自列 | Approved 2026-08-01 | ADR-009 | approved |
| OQ-B011 | **Collaboration ownership**：Provider/Core 精確路徑、shared contract approval authority、adapter harness 與 PORT_CHANGE 流程是否核准 | current user ownership/proposal-flow direction | yes | 並行開發、contract governance、provider task authority | Core Maintainer + Provider Owners | 採 ADR-010 的精確 ownership 與 Core-only shared-contract approval | Approved 2026-08-01 | ADR-010 | approved |
| OQ-B012 | **Transaction semantics（額外 blocking）**：分散式 Task rate-limit、create-or-get、24h window 與 Formal quota 的 transaction/key/consistency 設計 | Master FR-001/011；CP-FR001/011 | yes | Production persistence adapter, DynamoDB transaction/key/index design, distributed concurrency and formal quota integration | Core Maintainer + Persistence Provider Owner | **Maintainer Proposed Default:** conditional transaction、相同 operation ID 重送、strong invariant checks；具體 resource design 不在 core contract | Before production persistence adapter implementation | future Persistence ADR | proposed_pending_human_approval |
| OQ-B013 | **Live extension provider（額外 blocking）**：credential/readiness contract 與官方資料 precision 對齊策略 | Master 4.3–4.4、FR-007 | yes | Production live-extension adapter, provider credentials/readiness, and production-complete reporting beyond 2026-05-31 | Market Data Provider Owner + Core Maintainer | **Maintainer Proposed Default:** provider 必須輸出 UTC/USDT、同 schema、Decimal canonical adapter；不得替換官方基準 | Before live market provider implementation | future Live Data ADR | proposed_pending_human_approval |
| OQ-B014 | **Dedup rules（額外 blocking）**：press/domain allowlist 完整值、SimHash/MinHash threshold、event window、representative tie-break | Master FR-006 | yes | Production dedup ruleset, press/domain allowlist, similarity thresholds, event clustering, representative tie-break, and independence scoring | Trust/Dedup Owner + Core Maintainer | **Maintainer Proposed Default:** 所有值版本化；未核准不得宣稱 production-complete | Before production dedup and independence implementation | future Trust/Dedup ADR | proposed_pending_human_approval |
| OQ-B015 | **Trust/confidence rules（額外 blocking）**：source trust、relevance、freshness、consistency、contradiction severity 與 final confidence 公式/門檻 | Master FR-008 | yes | Production trust, relevance, freshness, consistency, contradiction severity, and final confidence rulesets | Trust/Reasoning Owner + Core Maintainer | **Maintainer Proposed Default:** 可拆解 deterministic components；不得以 opaque LLM score 取代 | Before production trust/confidence implementation | future Trust/Confidence ADR | proposed_pending_human_approval |

## Nonblocking

| ID | description | source | blocking yes-no | blocks | owner | proposed default | decision deadline | resolution ADR | status |
|---|---|---|---|---|---|---|---|---|---|
| OQ-N001 | React/Amplify 與本機 Streamlit 的並存、遷移及 progress transport | Master architecture/UI references；core design OQ | no | production UX/deployment only | UI Owner | **Maintainer Proposed Default:** 保留 API/use-case boundary；開發期可保留 Streamlit，production UI 再選 React transport | Before production deployment | future UI ADR | open |
| OQ-N002 | Bedrock/SageMaker 精確 model ID 與 region | Master AWS/model names | no | production provider configuration | Provider Owners + Cloud Owner | **Maintainer Proposed Default:** 以部署時 capability discovery 與 allowlisted config 指定，不寫入 core schema | Before production deployment | provider deployment decision | open |
| OQ-N003 | Artifact signed URL TTL | Master authorized short-lived download intent；integration rules | no | production download authorization | Security Owner + Artifact Provider Owner | **Maintainer Proposed Default:** 使用最小可行短效 TTL，值由 production threat model 決定 | Before production deployment | future Artifact Security ADR | open |
| OQ-N004 | Raw/Evidence/Event/Artifact retention days | Master persistence/retention references | no | production lifecycle/cost/compliance | Data Governance Owner | **Maintainer Proposed Default:** 不在未核准前自動永久保存；各資料類別分別定義 | Before production deployment | future Retention ADR | open |
| OQ-N005 | DynamoDB table/index/resource names | Master AWS component names；ADR-010 boundary | no | production deployment naming | AWS Persistence Provider Owner | **Maintainer Proposed Default:** resource names 留在 Infrastructure/config，不進 Domain/Application contract | Before production deployment | provider deployment decision | open |

## Resolution rule

- 未來的 `proposed_pending_human_approval` 不得被實作完成、測試通過或文件存在自動改成 Approved。
- Blocking 項目只有 owner 明確決議後，才能更新對應 ADR Status/Port contract；在此之前均是 **Maintainer Proposed Default**。
- 若新問題只由亂碼或不可可靠判讀文字產生，source 必須標 `SOURCE_TEXT_UNCERTAIN`，不得補猜答案。
