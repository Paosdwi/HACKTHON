# CryptoTrust Agent — Structure Steering

## 目的與狀態

本文件固定 Clean Architecture、Core/Provider ownership 與變更 authority。這是目標責任配置，不代表本輪建立產品程式。ADR-010 已由 Maintainer 於 2026-08-01 核准。

## 目標結構

```text
.
├── CRYPTO_TRUST_AGENT_MASTER_SPEC.md
├── .kiro/
│   ├── steering/
│   └── specs/
│       ├── core-platform/
│       │   ├── requirements.md
│       │   ├── design.md
│       │   └── tasks.md
│       └── provider-adapters/
│           ├── requirements.md
│           ├── design.md
│           └── tasks.md
├── docs/
│   ├── architecture/
│   │   ├── adr/
│   │   ├── schemas/
│   │   ├── integration-rules.md
│   │   ├── open-questions.md
│   │   ├── ports-and-schemas.md
│   │   └── source-confidence-register.md
│   └── proposals/
├── src/crypto_trust_agent/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   │   ├── collectors/
│   │   ├── extraction/
│   │   ├── reasoning/
│   │   ├── market_regime/
│   │   ├── persistence/
│   │   ├── events/
│   │   ├── artifacts/
│   │   ├── observability/
│   │   ├── aws/
│   │   ├── identity/
│   │   └── fakes/
│   └── presentation/
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    │   ├── core/
    │   └── providers/
    ├── architecture/
    ├── e2e/
    └── fixtures/
```

不得以 provider／vendor 名稱另建 alternate infrastructure directory；能力只使用 `collectors`、`extraction`、`reasoning`、`market_regime`、`persistence`、`events`、`artifacts`、`observability`、`aws`。

## Clean Architecture 責任

### Domain

擁有 entity、immutable value object、state machine、deterministic formula、Trust/Contradiction policy、domain error/event。不得放 HTTP model、SDK、env/config、network/filesystem、JSON wire concern。

### Application

擁有 use cases、orchestration、deterministic Planner、preflight/quota/publication workflow、十一個 Port 與 boundary DTO。不得依賴 concrete adapter、FastAPI 或 SDK。

### Infrastructure

實作 Port、provider mapper、vendor error mapping、deadline/cancellation、serialization、persistence、network、queue、storage、observability 與 composition support。Vendor type 不得洩漏至 Application/Domain。

### Presentation

擁有 FastAPI routes/dependencies、verified identity mapping、HTTP error/response shaping 與 demo UI。Route 只呼叫 use case，不直連 repository/provider。

## Ownership matrix

| Path / resource | Authority | Rules |
|---|---|---|
| `src/crypto_trust_agent/domain/` | Core | Provider 不得修改 |
| `src/crypto_trust_agent/application/` | Core | 含 Port/DTO；Provider 不得修改 |
| `src/crypto_trust_agent/presentation/` | Core | Provider 不得修改 |
| `src/crypto_trust_agent/infrastructure/identity/` | Core | JWT／principal boundary |
| `src/crypto_trust_agent/infrastructure/fakes/` | Core | Core fake composition；Provider 可供 fixture proposal，不直接取得 authority |
| `src/crypto_trust_agent/infrastructure/{collectors,extraction,reasoning,market_regime,persistence,events,artifacts,observability,aws}/` | Provider | 僅自身 capability module/mapper；不可改其他 capability 或 shared contract |
| `tests/unit/`, `tests/architecture/`, `tests/e2e/` | Core | Core acceptance authority |
| `tests/contract/` shared schemas/assertions | Core | Provider 不得修改 expected semantics |
| `tests/contract/` adapter harness/fixtures/evidence | Provider | 只可新增 provider-specific harness，仍須跑 shared assertions |
| `tests/integration/core/` | Core | Core-owned API、dataset、repository/use-case 與 UI integration；一般 CI 不連真實 provider/AWS |
| `tests/integration/providers/` | Provider | 真實 provider/AWS；顯式 opt-in；不得使用 `tests/integration/core/` 或 generic integration path 規避 ownership |
| `.kiro/specs/core-platform/` | Core | Core task authority |
| `.kiro/specs/provider-adapters/` | Provider plan | `tasks.md` 是 Provider 進度唯一權威 |
| `docs/architecture/` shared contracts/ADR | Core Maintainer | Provider 只可經 proposal 請求變更 |
| `docs/proposals/` | Proposal submitter | 不等同 contract approval |

## Port 與 DTO 放置

- Application interfaces 位於 `application/ports/`；DTO 位於 `application/dto/` 或對應 port module。
- 人類索引：`docs/architecture/ports-and-schemas.md`；machine authority：`docs/architecture/schemas/**/contract.schema.json`。
- Adapter-specific wire payload/SDK model 留在 Infrastructure mapper。
- Boundary payload 帶 `schema_version`；禁止無版本 dict 或 SDK object 穿越。

## Provider change flow

若 Port 無法表達 provider 需求：

1. 建立 `docs/proposals/PORT_CHANGE_<name>.md`。
2. 必須包含：Problem、Proposed change、Compatibility impact、Testing requirements、SemVer impact。
3. 等待 Core Maintainer review；未核准前不得改 shared contract/schema/assertions、Domain/Application DTO 或以 untyped extension 繞過。
4. 核准後由 Core 更新 schema、shared tests 與 migration；Provider 再更新 adapter。

## Task authority

- Core 實作狀態只在 `.kiro/specs/core-platform/tasks.md` 維護。
- Provider adapter 狀態只在 `.kiro/specs/provider-adapters/tasks.md` 維護。
- Core T70–T74 只能是 cross-plan reference，不得複製 checkbox、acceptance 或 completion authority。
- 若兩份 plan 不一致，停止實作並提交 Core reference 修正；不得以 Core reference 勾選 Provider 工作。

## Test 路徑

- `tests/unit/`：無 network/AWS/wall-clock dependence；使用 injected Clock。
- `tests/contract/`：shared suite + provider harness；權限依 ownership matrix。
- `tests/integration/core/`：Core-owned API、dataset、repository/use-case、UI integration；使用 fake/local boundary，不連真實 provider/AWS。
- `tests/integration/providers/`：Provider-owned 真實 provider/AWS integration，受控 credential、顯式 opt-in。
- `tests/architecture/`：import、禁止 alternate provider directory、ownership checks。
- `tests/e2e/`：fake adapters，三題型與降級；不連 AWS。
- `tests/fixtures/`：去識別、無 secret、最小且授權可用。

## 文件與版本

1. 需求／契約先改 requirements/design/schema/tasks，經核准後才改程式。
2. Breaking schema change 需 SemVer major、migration 與 compatibility evidence。
3. JSON field `snake_case`；UTC RFC 3339 `Z`；hash 明示演算法。
4. schema、planner、trust、formula、fingerprint、model/ruleset 各自版本化。
5. 本輪只改 Spec/ADR/schema 與協作邊界文件，不建立或修改產品程式、不執行 Phase 1+ task、不連 AWS。

Open Questions 的唯一清單是 `docs/architecture/open-questions.md`。
