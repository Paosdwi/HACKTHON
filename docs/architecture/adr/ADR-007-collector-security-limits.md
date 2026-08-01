# ADR-007: Collector Security Limits

## Status

Approved — 2026-08-01

## Context

FR-004/安全需求要求 allowlist、DNS/IP/SSRF、redirect、payload、timeout、robots 與 rate limits，但 Master 未唯一提供所有精確值。**本 ADR 的每一項限制（包含所有數值與 policy）都是 Maintainer Proposed Default**，必須經 human/security approval 才能成為 production baseline。

## Decision

Collector policy `collector-security-1.0.0` 採以下 **Maintainer Proposed Default**：

1. 只允許 HTTPS，目的 port 只允許 443；禁止 URL credentials（`userinfo@host`）。
2. 完整 input URL 長度最多 2048 characters；超限在 DNS/network 前拒絕。
3. 最多跟隨 3 次 redirect；每一 hop 都重新執行 scheme/port/credentials、allowlist、DNS resolution 與 IP classification，不信任初始 host 的結果。
4. DNS/IP 解析後拒絕 private、loopback、link-local、reserved、multicast 與 cloud metadata destinations；IPv4/IPv6、literal IP、DNS rebinding/多 A/AAAA answer 都適用。任一實際連線 IP 必須在核准 public set 中。
5. Static HTTP connect timeout 3 秒、read timeout 10 秒、total timeout 15 秒；total 是外層 hard guard，不因 redirect/retry 重置。
6. Playwright job total timeout 30 秒，涵蓋 browser/context/page creation、navigation、render 與 extraction；不得在背景繼續。
7. Raw decompressed response/body 最多 5 MiB；以 decompressed bytes enforce，壓縮比不得繞過。Cleaned content 最多 1 MiB，超限拒絕或依已版本化 deterministic truncation policy處理，不交給模型自行截斷。
8. Per-host concurrency 最多 2；同 host request start 間隔至少 1000 ms。Redirect target 以其自身 host 限制計算。
9. 必須遵守 robots policy 與版本化 domain/source allowlist；robots 不允許或 host 不在 allowlist 即拒絕。Web Grounding discovery 不構成 allowlist approval。
10. 所有安全拒絕回 typed safe reason；不得由 Nova、Reasoning、client 或 provider extension 覆寫。

## Alternatives considered

- HTTP fallback：增加降級與中間人風險。
- 允許任意 port：擴大 SSRF/internal service surface。
- 只驗初始 URL：redirect 可繞過 allowlist/IP policy。
- 只看 compressed size：compression bomb 可繞過。
- 只依 SDK timeout：無 total outer guard。
- robots/allowlist advisory：不符合既定安全邊界。
- unlimited per-host parallelism：違反來源禮貌與資源控制。

## Consequences

部分合法但非 HTTPS/443、較慢、較大或 robots-disallowed 的來源會被拒絕；系統應保留 failed/skipped/limitation，而非繞過 policy。Playwright 有獨立較長 total，但仍受 command/stage/global deadline 的更早限制。

## Compatibility impact

現有 collector 若接受 HTTP、non-443、長 URL、更多 redirects/payload 或較高 concurrency，需在 adapter boundary 收緊。Policy version 必須進 RawRecord security metadata/log；變更數值需 ADR/review，不可 provider-local drift。

## Security impact

本決策直接降低 SSRF、metadata theft、DNS rebinding、redirect pivot、credential leakage、decompression bomb、slow response 與 abusive crawling。Allowlist/robots data 本身需 integrity/version control；DNS validation 必須與實際 socket connection 綁定，避免 resolve/check/use 差異。

## Testing requirements

- HTTPS/443 accept；HTTP、non-443、URL credentials、2049-char reject-before-network tests。
- redirect 0/3/4、每 hop allowlist/DNS/IP revalidation 與 HTTPS→unsafe redirect tests。
- IPv4/IPv6 private/loopback/link-local/reserved/multicast/metadata、literal/encoded/rebinding/multiple-address negative tests。
- connect 3s/read 10s/static total 15s/Playwright total 30s deterministic timeout tests。
- compressed≤limit but decompressed>5MiB、cleaned 1MiB boundaries tests。
- host concurrency=2、1000ms spacing、redirect-host independent limiter tests。
- robots/allowlist deny、Web Grounding cannot override、typed redacted audit tests。
- cancellation 後 browser/network/background write 終止測試。

## Open Questions

- 完整 domain allowlist、robots cache TTL 與 user-agent identity 需 Security/Legal approval。
- Public IP classification library/version 與 cloud metadata range feed 需 Provider proposal。
- Cleaned >1MiB 採 reject 或 deterministic truncation，需與 extraction/context schema 一致決議。

## Source confidence

Master 可直接支持 collector 安全控制類型，但不唯一支持精確 policy 或數值。HTTPS/443、URL/redirect/DNS/time/payload/rate/robots 的具體限制均是 architecture design inference，因此維持 **Maintainer Proposed Default**。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
