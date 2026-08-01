# ADR-009: Artifact Formats and Minimum Publishable Bundle

## Status

Approved — 2026-08-01

## Context

FR-010 要求 Final Report JSON/Markdown/HTML、Evidence List JSON/CSV、Execution Log JSONL 與 Manifest；deadline degradation 又要求最低合格 bundle。Master 未唯一指出 degraded publication 的必要子集與 Manifest 自我列舉。以下均為 **Maintainer Proposed Default**。

## Decision

1. Normal successful bundle 仍必須包含全部格式：
   - Final Report JSON、Markdown、HTML；
   - Evidence List JSON、CSV；
   - Execution Log JSONL；
   - Manifest JSON。
2. Degraded minimum publishable bundle 精確為四檔：Final Report JSON、Evidence List JSON、Execution Log JSONL、Manifest JSON。
3. 四個 minimum artifacts 缺任一個，或任一個未通過 schema/citation/numeric/lineage/hash validation，均不得 publication；Execution 必須安全失敗，不能以其他 renderer 格式替代。
4. Markdown、HTML 或 CSV renderer 失敗時，只要四檔 minimum bundle 全部有效，可 publication 並將 Execution 標記 `partial`；每個缺失格式都要有 safe reason。
5. Manifest 最後生成，必須列出 `available` artifacts（type、format、MIME、schema version、generated time、size、SHA-256）、`missing` expected artifacts 與每項 `reasons`。Missing artifact 不得虛構 hash。
6. Manifest 不列出自己；其 descriptor/hash 可由 repository/API response 或外層 transport metadata 提供，不做 recursive self-hash。
7. Final Report JSON 是 Markdown/HTML 唯一 canonical source；Evidence List JSON 是 CSV 唯一 canonical source。Renderer 不得改變 canonical semantics。
8. Publication 是 terminal visibility gate：四檔 minimum bundle 與 Manifest 完成前，不得向一般 client 顯示已發布報告。

## Alternatives considered

- 只需 Final Report JSON：缺少 evidence/log/manifest，無法回溯與驗證。
- renderer 任一失敗就完全失敗：失去 canonical valid output 的可用性。
- CSV/MD/HTML 可替代 canonical JSON：語意與 schema 不穩定。
- Manifest 列自己：形成 recursive hash/two-pass ambiguity。
- 先發布再補 Manifest：client 可能看到不完整 bundle。

## Consequences

Deadline degradation 有可測試 publication floor；renderer failure 不會丟失 canonical evidence。Manifest 明確呈現 missing formats，normal success 仍不能省略任何指定格式。需要 atomic/visibility semantics 確保 bundle 未完成前不可見為 published。

## Compatibility impact

既有只產生部分 artifacts 的流程不能標 `succeeded`；若具四檔 minimum 可標 `partial`，否則 fail。Manifest schema 需加入 available/missing/reasons，並移除/禁止自我列舉。舊 consumer 應依 schema version migration。

## Security impact

每檔 hash、schema、lineage 與 publication gate 防止 tampered/incomplete report 被視為正式產出。Execution Log 仍須 redacted；Manifest reason 必須安全，不含 stack trace、secret、signed URL credential 或 raw content。

## Testing requirements

- Normal bundle 七個檔案/格式完整性與 canonical renderer golden tests。
- 四檔 minimum 的每一個 single-missing permutation 都不得 publication。
- MD/HTML/CSV 各自或組合 failure → partial、missing/reasons 正確。
- canonical JSON invalid/citation/numeric/lineage/hash mismatch → no publication。
- Manifest last-write ordering、available hashes/sizes/MIME/schema/time、missing no fake hash、not self-listed tests。
- concurrent/retry same key+hash no-op、different hash conflict、visibility gate fault injection。
- reason/log redaction canary tests。

## Open Questions

- Bundle visibility 使用 repository transaction、publish marker 或 immutable prefix switch，由 Artifact Port/Persistence design 決定。
- Manifest 本身 hash 的 API/transport descriptor shape 需 schema review。
- PDF 等未來格式只能新增為 optional/normal expected format，不能取代 minimum bundle。

## Source confidence

完整 normal formats 可由 Master FR-010 直接確認；degraded 四檔 minimum、publication gate 與 Manifest available/missing/not-self-listed 是 architecture design inference，均為 **Maintainer Proposed Default**。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
