# ADR-008: Structured Reasoning Context Bounds

## Status

Approved — 2026-08-01

## Context

FR-009 要求 Reasoning Provider 只接收已驗證、有界、task-scoped 且不含 secret/raw HTML 的 Context，但 Master 未唯一指定 bytes/tokens/record/string limits。以下均為 **Maintainer Proposed Default**。

## Decision

Context ruleset `reasoning-context-1.0.0`：

1. RFC 8785 canonical JSON 的 UTF-8 encoded size 必須 `<= 524288` bytes。
2. 實際 provider tokenizer 計算的 model input 必須 `<= 64000` tokens；估算值不能取代目標 provider/tokenizer 的 preflight count。
3. `evidence_refs` 最多 120；`analysis_refs` 最多 32；`contradictions` 最多 64；`limitations` 最多 50。
4. 每個 excerpt/value 的長度最多 2000 Unicode scalar values；`question` 最多 2000 Unicode scalar values。
5. 禁止 raw HTML、secret、token、Authorization header、signed URL credential、完整 prompt history、chain-of-thought、跨 Task reference 與 quarantined content。
6. Builder 必須先完成 task lineage、assessment selection、citation target、numeric/schema 與 forbidden-content validation，再做 deterministic rank/truncate。
7. Ranking/truncation 必須由版本化 deterministic tuple 執行，不由 LLM 決定。**Maintainer Proposed Default ranking**：requirement priority；counter/contradiction preservation；relevance；source trust；freshness；independence representative；stable ID ascending tie-break。
8. 若超過任何 count/string/byte/token limit，依 rank 移除最低項或 deterministic 截短文字，並記錄 `omissions`：category、omitted count/IDs（可安全時）、reason、original/final bytes/tokens/counts、ruleset version。不得靜默省略。
9. Byte 與 token gate 都必須通過；無法取得正確 provider tokenizer 時不得送 production reasoning request。

## Alternatives considered

- 只限制 token：canonical payload/storage/network 仍可能過大。
- 只限制 bytes：不同 tokenizer 可能超 model limit。
- LLM 自行挑 evidence：破壞 deterministic plan/audit。
- first-N truncation：偏向收集順序並可能刪掉 counter-evidence。
- raw HTML 直接送模型：增加 injection、token 與資料洩漏風險。
- truncation 不記錄：報告無法揭露 context coverage。

## Consequences

Reasoning cost/latency/attack surface 有上限，且 selection 可重現。大量 evidence 可能被省略，因此 Final Report/Execution Log 必須可見 omissions；limits 並不改變完整 EvidenceRepository 的保存。Tokenizer/version 成為 provider capability 的一部分。

## Compatibility impact

現有無 bounds 的 Context builder 必須加入 canonical byte/token/count validation 與 omissions schema。Provider adapter 不能以自己的 silent truncation 取代 core builder；若 provider limit 更小，須提出 Port/ADR 變更或 capability rejection。

## Security impact

禁止 raw HTML/secret 與 deterministic bounds 降低 prompt injection、data exfiltration 與 resource exhaustion。Forbidden-content scanner/redaction failure 必須 fail closed；omissions 不得洩漏被排除的 secret/raw content。

## Testing requirements

- 524288/524289 bytes、64000/64001 provider-token boundaries。
- evidence 120/121、analysis 32/33、contradiction 64/65、limitation 50/51、question/excerpt 2000/2001 Unicode scalar tests。
- multibyte/combining/surrogate handling tests，scalar count 與 UTF-8 byte count 分離。
- deterministic rank/tie-break property tests；input permutation 得相同 selected set/canonical JSON。
- counter-evidence/contradiction preservation fixtures 與 omission completeness tests。
- raw HTML/secret/token/header/signed URL/cross-task/quarantined negative tests。
- exact tokenizer unavailable/model version mismatch 時 fail closed。

## Open Questions

- Ranking tuple 的 precise enum weights/ordering 是否需獨立 trust ruleset 對齊。
- Provider tokenizer artifact/version 的發佈與 cache policy。
- 若有效 question 本身 >2000 scalars，request boundary 應先拒絕或另存完整原文，需 API schema 決定。

## Source confidence

Master 的 bounded、task-scoped、no-secret/no-raw-HTML obligation 可直接確認；所有 bytes/token/count/string 數值、ranking/truncation 與 omissions shape 是 architecture design inference，均為 **Maintainer Proposed Default**。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
