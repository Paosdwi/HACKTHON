# ADR-010: Core and Provider Collaboration Ownership

## Status

Approved — 2026-08-01

## Context

Core 必須可與 Collector、Extraction、Reasoning、Market Regime 與 AWS adapter 協作者並行開發，又不能讓 Provider 改寫 Domain/Application 或共用 contracts。使用者已明示 ownership 與 proposal flow；其 source confidence 為 confirmed，且本 ADR 已於 2026-08-01 取得 Maintainer 人工核准。

## Decision

### Provider owns

Provider ownership 精確限於下列路徑：

- `src/crypto_trust_agent/infrastructure/collectors/`
- `src/crypto_trust_agent/infrastructure/extraction/`
- `src/crypto_trust_agent/infrastructure/reasoning/`
- `src/crypto_trust_agent/infrastructure/market_regime/`
- `src/crypto_trust_agent/infrastructure/persistence/`
- `src/crypto_trust_agent/infrastructure/events/`
- `src/crypto_trust_agent/infrastructure/artifacts/`
- `src/crypto_trust_agent/infrastructure/observability/`
- `src/crypto_trust_agent/infrastructure/aws/`
- `tests/contract/`
- `tests/integration/providers/`
- `.kiro/specs/provider-adapters/`

Provider 在上述 infrastructure 路徑擁有 capability adapter、provider payload mapper、vendor error mapping、deadline/cancellation 實作、provider-specific config 與 conformance evidence。`infrastructure/aws/` 是 shared physical directory；Provider 只能修改自身 capability-specific module/mapper，不得修改其他 capability 或 Core contract。不得另建 provider-name alternate Infrastructure directories 或其他 alternate provider directories 來繞過上述 ownership。

`tests/contract/` 允許 Provider 寫 adapter harness、provider fixtures 與 conformance evidence；此 directory ownership 不授予 Provider 修改共用 contract/schema、shared assertions 或 expected semantics 的權限。Provider integration tests 只能置於 `tests/integration/providers/`；generic `tests/integration/` 不屬 Provider authority。`docs/specs/` 不屬 Provider authority。

### Core owns

Core ownership 精確包含：

- `src/crypto_trust_agent/domain/`
- `src/crypto_trust_agent/application/`
- `src/crypto_trust_agent/presentation/`
- `src/crypto_trust_agent/infrastructure/identity/`
- `src/crypto_trust_agent/infrastructure/fakes/` 中的 core fake composition
- `tests/unit/`
- `tests/architecture/`
- `tests/e2e/`
- `tests/integration/core/`
- `.kiro/specs/core-platform/`
- `docs/architecture/` 中的共用契約

Core 擁有 Ports/DTO、shared schemas、state/deadline/retry/idempotency semantics、core fake parity、shared contract assertions、publication rules 與 architecture boundaries。Provider 不得把 SDK/vendor type、credential、provider retry loop、provider branch 或 transport concern 帶入 Domain/Application。

### Shared contract and schema approval

1. 共用 contract/schema 只能由 **Core Maintainer** 修改或核准。
2. Provider 不得直接改 method signature、DTO field/enum/error、semantic invariant、shared assertion 或 shared expected result。
3. Provider 可在 `tests/contract/` 新增 adapter harness，但 harness 必須消費已核准的共用契約，不能重新定義契約。
4. 未核准時不得以 arbitrary `dict`、`extensions` 濫用、feature flag、SDK object 或 provider-local monkey patch 繞過共用契約。

### Port change proposal

若現有 Port 不足，Provider 必須先建立 `docs/proposals/PORT_CHANGE_<name>.md`，至少包含下列精確 sections，並等待 Core Maintainer 核准後才可修改 shared contract 或依賴新行為：

1. `Problem`
2. `Proposed change`
3. `Compatibility impact`
4. `Testing requirements`
5. `SemVer impact`

Proposal 必須列出受影響 Port/schema/version、migration/dual-read（如需）、provider evidence 與 rejection fallback。提交 proposal 不代表 Provider 擁有 `docs/` 或共用 contract；核准、SemVer 與 migration 決定權仍只屬 Core Maintainer。

### Task authority

Provider adapter 工作的唯一 task authority 是 `.kiro/specs/provider-adapters/`，其中執行清單為 `.kiro/specs/provider-adapters/tasks.md`。Core task 中的 T70、T71、T72、T73、T74 只保留 cross-plan reference/dependency，不複製 provider acceptance、進度或 completion authority；若兩處不一致，以已核准的 provider-adapters task 為準並回報 Core reference 修正。

## Alternatives considered

- Provider 直接修改 Port：速度快但破壞 substitutability/parallel safety。
- 把 `docs/specs/` 或 generic `tests/integration/` 交給 Provider：會擴張到共用規格或非 Provider integration authority。
- 為 Bedrock/SageMaker 建 alternate directories：會造成 capability ownership drift 與重複 adapter boundaries。
- Core 擁有所有 adapter code：形成瓶頸且模糊 Provider accountability。
- 每份 plan 都複製 T70–T74：會產生進度與 acceptance drift。
- 用 untyped extensions 解決缺口：規避 schema/SemVer/test review。
- 只用口頭/PR 描述 change：缺少 compatibility 與 test evidence。

## Consequences

Provider 可在明確 capability directory 並行交付，Core contract 有單一核准者。Provider 仍可在 `tests/contract/` 交付 adapter harness，但無權改共用契約。Port gap 先進 proposal/review，短期較慢但避免 adapter-driven contract drift。Provider spec 與 integration authority 不會擴散到 Core specs、`docs/specs/` 或 generic integration tests。

## Compatibility impact

現有 Provider 若已修改 shared contract，必須回退為 proposal 並由 Core Maintainer 決定 SemVer/migration。位於 `tests/integration/` 的 Provider-specific tests 應遷至 `tests/integration/providers/`；任何位於 alternate Bedrock/SageMaker infrastructure directories 的實作應遷回核准 capability 路徑。Core T70–T74 的內容需在後續獲准的 spec maintenance 中縮減為 references；本 ADR 不直接修改 `.kiro`。Shared contract 的 breaking change 仍需 major version 與 migration。

## Security impact

權責隔離降低 Provider 將 secret、SDK type、不受控 retry/network 或較弱 validator 帶入 Core 的風險。Proposal/approval 與 shared tests 提供供應鏈和變更 audit。Provider-specific integration tests 必須 opt-in，fixture 不含 secret/raw sensitive data。

## Testing requirements

- Architecture tests：Provider directory 不被 Domain/Application import；SDK types 不穿越 Port。
- Ownership checks：Provider changes 限於核准 infrastructure capability paths、`tests/contract/` adapter harness、`tests/integration/providers/` 與 `.kiro/specs/provider-adapters/`。
- Negative path checks：`docs/specs/`、generic `tests/integration/`、任何 provider-name alternate Infrastructure directory 不得成為 Provider authority。
- Review-policy equivalent checks：shared contract/schema change 必須有 Core Maintainer approval evidence。
- 每個 Provider adapter 跑相同 shared contract suite，並可增加 provider-specific opt-in integration tests。
- Negative governance fixture：未核准 DTO/enum/error/shared assertion change 應被 schema/contract CI 拒絕。
- `PORT_CHANGE_<name>.md` lint 驗證五個必要 sections 與 SemVer classification。
- Task traceability audit：Provider authority 項目唯一，Core T70–T74 只有 reference。
- Shared `infrastructure/aws/` module boundary/import tests，避免 capability cross-ownership。

## Open Questions

- `.kiro/specs/provider-adapters/tasks.md` 尚需由獲准的 spec workflow 建立/驗證；在此之前不得宣稱 provider tasks 已遷移完成。
- Repository 的 review enforcement 要由 CODEOWNERS、CI policy 或 maintainer process 實現，需 human decision。
- `tests/contract/` 中 shared assertions 與 Provider adapter harness 的機械式檔名/目錄隔離方式，需由 Core Maintainer 定義；不影響本 ADR 的 approval authority。

## Source confidence

使用者明示的 Provider/Core 精確 ownership、Core-only shared contract/schema approval、Provider 在 `tests/contract/` 的 adapter harness 權限、PORT_CHANGE proposal flow 與 provider-adapters task authority 是 `confirmed` direction。Source confidence 本身不等於 ADR approval；本 ADR 已由 Maintainer 於 2026-08-01 另行人工核准。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
