# ADR-003: Distributed Deadline Propagation

## Status

Approved — 2026-08-01

## Context

Formal Run 有 900 秒 hard deadline，且 core 需要 monotonic 計時；不同 process/runtime 的 monotonic epoch 不可互相比較。現有 draft 傳遞 monotonic absolute value，無法安全跨 Step Functions、Lambda、HTTP 或 SDK boundary。以下 wire 與接收規則均為 **Maintainer Proposed Default**。

## Decision

1. Wire `DeadlineDTO` 精確包含：

```json
{
  "schema_version": "1.0.0",
  "operation_id": "OP-001",
  "deadline_at_utc": "2026-08-01T02:15:00Z",
  "budget_ms": 25000,
  "sent_at_utc": "2026-08-01T02:14:34Z",
  "safety_margin_ms": 1000
}
```

2. `safety_margin_ms` default `1000`，最小 `100`，最大 `5000`；超出範圍的 DTO 拒絕，不靜默擴大 deadline。
3. Receiver 取得同一瞬間的 `now_utc` 與自身 `now_monotonic`，計算：

```text
utc_remaining_ms = deadline_at_utc - now_utc
usable_utc_ms = max(0, utc_remaining_ms - safety_margin_ms)
effective_ms = max(0, min(provider_timeout_ms, budget_ms, usable_utc_ms))
local_deadline_monotonic = now_monotonic + effective_ms
```

4. `effective_ms <= 0` 時不得啟動 I/O，回 `deadline_exceeded`。`sent_at_utc` 用於 schema/staleness/audit，不用來比較不同 process 的 monotonic time，也不得增加剩餘時間。
5. 不同 process 的 monotonic values 永不傳遞或比較；每個 receiver 只建立並使用自身 local monotonic deadline。
6. Step Functions、Lambda runtime、HTTP client/server 與 provider SDK 都要設外層保護；SDK timeout 只是內層限制，不能是唯一 deadline guard。每層 timeout/cancellation 必須不晚於 local deadline。
7. Downstream forwarding 使用當下重新計算的 remaining budget、原 absolute UTC deadline 與新 `sent_at_utc`；不得重置為原始 budget。

## Alternatives considered

- 傳送 caller runtime 的 monotonic epoch 欄位：跨 process 無共同 epoch，禁止採用。
- 只傳 UTC absolute deadline：wall clock skew/回撥風險較大且缺少 operation budget。
- 只傳 relative budget：每一 hop 可能重置或忽略 global deadline。
- 只依賴 provider SDK timeout：無法涵蓋 queue/runtime/network 外層卡住。
- 固定 margin 不設 bounds：無法針對 operation 表達且容易配置失誤。

## Consequences

每個 process 可用自身 monotonic clock enforce deadline，同時以 UTC absolute deadline 保持跨 process 上限。Clock skew 仍可能提早結束，因此 safety margin 偏向 fail-safe；多層 outer guard 增加設定與取消測試成本。

## Compatibility impact

舊 `requested_at` + caller-runtime monotonic epoch draft wire 與 v1 不相容；adapter migration mapper 可在單一 process legacy boundary 暫時接受舊形，但 core v1 contract 只產生/接受新六欄結構。變更需 Port schema/version review。

## Security impact

有界 deadline 降低資源耗盡與 timeout amplification。Receiver 不信任來自 client/provider 的延長值；只有 core 可建立可信 DeadlineDTO。Log 不應包含 provider secret，只記 operation、effective budget 與 safe timeout reason。

## Testing requirements

- exact DTO schema、UTC RFC3339、integer/nonnegative budget 與 margin 100/1000/5000 邊界 tests。
- property test：effective 永不大於三個 input limits，且不為負。
- wall clock skew/rollback 模擬下 local monotonic 不倒退；跨 process monotonic epoch 完全不同仍正確。
- transit delay、expired deadline、zero budget、provider timeout 最小值 tests。
- Step Functions/Lambda/HTTP/SDK 每層 outer timeout/cancellation contract tests。
- forwarding 不重置 budget；cancel 後不得背景寫入無 lineage data。

## Open Questions

- 各 runtime 的最大可容忍 clock skew 與 clock-health alarm 門檻。
- queue 中過期 command 是丟棄或寫 terminal event 的 provider-specific mapping。
- provider timeout 不存在時使用哪個 capability default，須逐 Port 核准。

## Source confidence

900 秒與 core monotonic requirement 可由 Master 直接確認；使用者明示的 DeadlineDTO wire 欄位、跨 process 不比較 monotonic epoch、receiver-local monotonic enforcement 與 distributed propagation/outer-guard 原則是 `confirmed` direction。`safety_margin_ms` default `1000`、minimum `100`、maximum `5000` 則是 design inference，維持 **Maintainer Proposed Default**。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
