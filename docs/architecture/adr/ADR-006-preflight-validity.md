# ADR-006: Pre-flight Pass Validity and Atomic Consumption

## Status

Approved — 2026-08-01

## Context

FR-011 要求 Pre-flight 不消耗正式額度，通過後才可建立 Execution，但 Master 未唯一定義 pass validity、input/dependency binding、single-use 與原子競爭行為。以下均為 **Maintainer Proposed Default**。

## Decision

1. 成功 Pre-flight pass 的 TTL 固定為 60 秒，自 `checked_at` 起算；expiry instant 到達即過期（`now < expires_at` 才有效）。到期後不得建立 Execution，必須重新執行 Pre-flight。
2. Pass 必須不可變地綁定 `task_id`、`task_version`、`input_lock_hash` 與 `dependency_snapshot_hash`。任一值在 Execution create 時不一致即視為 stale/invalid。
3. `input_lock_hash` 覆蓋 canonical question、assets requested/canonical order、timeframe、question type、formal intent 與相關 ruleset versions；演算法/欄位 schema 需版本化。
4. `dependency_snapshot_hash` 覆蓋本次 required readiness checks 的 canonical result、capability/schema/model/service versions 與安全 reason status；不包含 secret。
5. Execution create 必須只透過 `ExecutionRepository.acquire_quota_and_create`，在單一原子 operation 內重新驗證 pass 存在、固定 60 秒 TTL、所有 binding（含 `input_lock_hash`）、未被消耗、Task input 可鎖定與 quota 可取得，然後鎖定 input、取得 quota、建立 Execution 並把 pass 標為 consumed。`TaskRepository` 不提供獨立 execution lock。
6. 一個 pass 只能成功建立一個 Execution。相同 operation/execution ID 重送必須回同一結果；不同 operation 競爭只能一個成功。
7. Pre-flight failure、not-ready、rate-limit、過期、stale、binding mismatch 與未成功建立 Execution 的 pass 均不消耗正式 quota。只有原子 transaction 成功建立 Execution 才消耗 quota。
8. 若原子 operation outcome 因 timeout 未知，caller 必須以相同 operation/execution ID 查詢或重送；不得產生新 ID 或先行消耗另一份 pass。

## Alternatives considered

- Pass 永久有效：dependency health 可能早已變化。
- 只綁 `task_id`：Task input/version 可在 pass 後改變。
- Execution create 分步檢查再寫入：併發可能雙重消耗或建立。
- Pre-flight 通過即消耗 quota：違反 FR-011。
- reusable pass：可對多個 execution 重放健康快照。
- create 失敗也消耗 quota：把 infrastructure race 當正式執行。

## Consequences

Execution start 有明確且短效的 readiness 證據，並防止 TOCTOU/double-create。60 秒可能要求使用者在延遲後重跑 Pre-flight，但重跑仍不消耗正式 quota且受獨立 rate limit。

## Compatibility impact

既有不含 pass ID/version/`input_lock_hash`/consumed state 的 Preflight DTO 與 repository contract 需升級。`ExecutionRepository.acquire_quota_and_create` 是跨 aggregate atomic revalidate + Task input lock + quota acquisition + Execution create + pass consume 的唯一 authority；`TaskRepository` 不提供獨立 `lock_for_execution` operation。移除該 method/CT ID 是為避免禁止的分步交易，屬 breaking draft change，仍待人工核准。

## Security impact

綁定 input/dependency snapshot 防止 pass 被移用到變更或不同 Task；single-use/atomic quota 防 replay。Snapshot/hash 不得包含 credential、endpoint secret 或 raw provider diagnostics。Authorization principal 與 task tenant scope 仍須在 transaction 前後驗證。

## Testing requirements

- 59.999s accept、60s boundary 定義與 >60s reject 的 fake-clock tests。
- task version/input/dependency 任一變更皆 stale；完全相同 binding accept。
- 100-way concurrent consume 只有一個 Execution/quota consumption。
- same operation ID retry 回同結果；unknown timeout recovery 不重複建立。
- failure/not-ready/rate-limit/expired/stale/repository rollback 均不消耗 quota。
- successful `ExecutionRepository.acquire_quota_and_create` 同時 revalidate pass、lock input、consume pass、acquire quota、create Execution；驗證 `TaskRepository` 無獨立 lock call，並執行 transaction/fault-injection tests。
- cross-task/cross-user replay rejection 與 secret-free snapshot tests。

## Open Questions

- 哪些 dependency status 變化需要 event-driven invalidation，而不只等待 TTL？
- 分散式 persistence 如何實現跨 record atomic invariant，由 Persistence ADR 決定。

## Source confidence

Master 可直接確認 Pre-flight 不耗正式 quota且通過後才建立 Execution；60 秒、各項 binding、atomic revalidation 與 single-use 是 architecture design inference，均為 **Maintainer Proposed Default**。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
