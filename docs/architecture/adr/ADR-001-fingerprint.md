# ADR-001: Request Fingerprint Canonicalization

## Status

Approved — 2026-08-01

## Context

FR-001 要求版本化 `request_fingerprint`，但 Master Spec 未唯一支持 Unicode、空白、排序、精確時間與 canonical JSON 的完整演算法。以下精確規則均為 **Maintainer Proposed Default**。Task 必須同時支援 fingerprint 的 set-like canonical assets 與雙資產流程所需的 requested order。

## Decision

採用 ruleset `fingerprint-1.0.0`：

1. `question`：先做 Unicode NFKC，再將一個以上 Unicode whitespace collapse 為單一 ASCII space，最後 trim；不得進行大小寫折疊、翻譯或標點改寫。
2. `assets`：每個值 uppercase；只接受 `BTC`、`ETH`、`SOL`、`BNB`、`XRP`；正規化後有重複值即拒絕，不以去重方式修復。
3. 另存 `assets_requested_order`；fingerprint payload 使用排序後的 `assets_canonical`。排序採大寫代碼的 ascending Unicode code-point order。
4. `timeframe.start` 與 `timeframe.end` 必須是有 timezone 的 RFC3339；轉成 UTC `Z` 後，秒與微秒必須為 `0`，否則拒絕。不得猜測無 timezone 或模糊時間。
5. Fingerprint payload 僅含 normalized `question`、`assets_canonical`、UTC `timeframe`。使用 RFC 8785 canonical JSON、UTF-8 bytes、SHA-256，digest 為 lowercase hex。
6. Wire/storage 格式固定為 `sha256:` 加 64 個 lowercase hex：`^sha256:[0-9a-f]{64}$`。
7. 必須隨 Task 保存 `fingerprint_ruleset_version: "fingerprint-1.0.0"`；既有 fingerprint 不因 ruleset 更新而原地重算。

## Alternatives considered

- Unicode NFC：較少相容字元折疊，但無法達成本提案的 NFKC identity。
- 保留所有 whitespace：使視覺相同問題形成不同 fingerprint。
- assets 保留 requested order 進 fingerprint：會讓同一 asset set 因 UI order 失去 idempotency。
- 一般 `json.dumps` 或欄位串接：跨 runtime 不保證同一 bytes。
- UUID/資料庫 key：不能重現 request identity。

## Consequences

相同 canonical input 可跨 runtime 重現；雙資產顯示/優先順序仍由 `assets_requested_order` 保留。NFKC 可能合併部分相容字元，且秒非零的請求會被拒絕而非截斷。規則更新需要新 ruleset 與明確 migration/dual-read 策略。

## Compatibility impact

Master 的合法整分鐘 RFC3339 JSON 可映射至本規則。舊 fingerprint 必須以其舊 ruleset 查找；不得無版本覆寫。若 client 傳秒/微秒非零、重複資產或非 allowlisted asset，v1 會拒絕。

## Security impact

穩定 canonicalization 降低 Unicode/whitespace 混淆與 quota bypass。SHA-256 fingerprint 不是 secret 或匿名化機制；若 question 具低熵，仍可能被猜測，因此 log 應最小化且不得用 fingerprint 取代 authorization。

## Testing requirements

- NFKC、全部 Unicode whitespace、trim 與標點保留的 table/property tests。
- asset lowercase→uppercase、allowlist、duplicate-after-normalization rejection、canonical/requested order tests。
- timezone offset→UTC、naive/ambiguous/non-zero second/microsecond rejection tests。
- RFC8785 cross-language golden vectors、UTF-8 與 SHA-256 lowercase format tests。
- 相同 canonical input 得相同 digest；任一 canonical field 改變應改 digest。
- ruleset migration、24h idempotency concurrency 與 quota-scope contract tests。

## Open Questions

- Human approver 是否接受 NFKC 對相容字元的 identity 合併？
- 未來 ruleset migration 要採 dual index 或只對新 Task 生效？
- 對 question 長度的上限由 request schema 另定，不在本 ADR 決定。

## Source confidence

Master FR-001 可直接支持版本化 request fingerprint/canonicalization obligation；NFKC、whitespace、asset/time canonicalization、RFC 8785/SHA-256 與精確 ruleset 是 architecture design inference，均為 **Maintainer Proposed Default**。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
