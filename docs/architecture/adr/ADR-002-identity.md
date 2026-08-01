# ADR-002: Trusted Identity, Admin Role, and Log Pseudonym

## Status

Approved — 2026-08-01

## Context

FR-001 要求可信 `user_id` 來自已驗證 Cognito JWT，並禁止 client override；Master 未唯一指定 claim、admin group 與 pseudonym 演算法。以下精確選擇均為 **Maintainer Proposed Default**。

## Decision

1. Application 的可信 user identity 使用已驗證 Amazon Cognito JWT 的 `sub` claim。
2. Boundary 在建立 `AuthenticatedPrincipal` 前必須驗證 issuer、audience、signature 與 `exp`；驗證失敗或缺少 `sub` 即拒絕。
3. 管理者資格只由已驗證 claim `cognito:groups` 是否包含精確字串 `CryptoTrustAdmins` 決定。Client body/query/path/一般 header/provider callback 不得授予 admin。
4. 任何 client 提供的 `user_id`、`sub`、role/group 或 identity override 一律拒絕，不忽略後繼續處理；授權與 repository tenant scope 只使用 trusted principal。
5. Log/event pseudonym 使用可輪替 secret key 的 HMAC-SHA-256 對 `sub` 計算。輸出需包含非秘密的 key version，例如 `hmac-sha256:k2026-01:<64-lowercase-hex>`；rotation key 由安全設定管理，不寫入 source/log。
6. Pseudonym 只供關聯 audit，不取代 authorization，也不得把原始 `sub`、JWT 或完整 claims 寫入一般 log。

## Alternatives considered

- 使用 email/username：可變且屬 PII。
- 未 keyed SHA-256(`sub`)：容易對已知 subject 做離線比對。
- Client 傳 user ID：破壞 tenancy 與 quota。
- 自訂 `admin=true` 或一般 `groups`：缺乏已驗證 Cognito claim boundary。
- 永不輪替的 HMAC key：增加長期洩漏影響。

## Consequences

`sub` 提供穩定 tenant identity；admin rerun 有單一可稽核來源。HMAC rotation 會使跨 key version 的 pseudonym 不同，因此查詢/audit 必須保留 key version，必要的受控 correlation 由安全工具執行。

## Compatibility impact

既有接受 body/query/header `user_id` 的 client 會被拒絕，必須改用有效 Cognito token。既有 log 若保存原 subject，需另行制定移除/retention migration；不得在本 ADR 中複製到新 pseudonym 欄位。

## Security impact

驗證 issuer/audience/signature/expiry 防止偽造或跨應用 token；固定 admin group 防 privilege escalation；HMAC pseudonym 降低 log 中直接識別與 dictionary attack 風險。Key rotation、least privilege 與 constant-time library 使用仍是 Infrastructure/security 責任。

## Testing requirements

- valid token 與 wrong issuer/audience/signature、expired、missing `sub` rejection tests。
- `cognito:groups` 包含/不包含、大小寫差異、錯誤型別及 forged client group tests。
- body/query/header/path identity override 全部拒絕的 API integration tests。
- 同 key+sub deterministic、不同 key version/不同 sub 不同 pseudonym、lowercase 64hex format tests。
- log redaction canary：JWT、raw `sub`、Authorization header、HMAC key 不得出現。
- admin technical rerun authorization/audit contract tests。

## Open Questions

- HMAC key rotation cadence、key storage、舊 key audit retention 由 Security Owner 核准。
- Cognito access token 與 ID token 中何者可被各 API 接受，需 deployment policy 明定。
- 多 user pool/tenant 是否需要把 verified issuer 納入 HMAC message，需 human decision。

## Source confidence

JWT-derived identity 與 client override prohibition 可由 Master FR-001 直接確認；`sub`、`CryptoTrustAdmins`、完整 validation set 與 keyed HMAC 是 architecture design inference，原為 **Maintainer Proposed Default**，已於 2026-08-01 核准。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
