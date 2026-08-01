# Provider Adapters Design

- **版本**：0.1.0
- **狀態**：Approved baseline；實作仍須依 Provider Tasks 執行
- **需求**：`requirements.md`
- **Task authority**：`tasks.md`

## 1. Boundary

```text
Provider SDK / HTTP / AWS service
        ↓
Provider-owned adapter + mapper + cancellation/error/redaction
        ↓ implements
Application-owned Port / DTO / typed error
        ↑
Core use case / orchestrator owns workflow, retry, fallback, outcome
```

Provider 不 import/修改 Core internals 以遷就 SDK；Core 不依賴 provider concrete implementation。Composition root 依核准 config 選 adapter。

## 2. Capability layout

| Capability | Provider-owned implementation | Provider tests |
|---|---|---|
| collection / Web discovery | `infrastructure/collectors/` + capability module in `infrastructure/aws/` | `tests/contract/` harness；`tests/integration/providers/` |
| evidence extraction | `infrastructure/extraction/` + capability module in `infrastructure/aws/` | same |
| reasoning | `infrastructure/reasoning/` + capability module in `infrastructure/aws/` | same |
| market regime | `infrastructure/market_regime/` + capability module in `infrastructure/aws/` | same |
| persistence | `infrastructure/persistence/` + capability module in `infrastructure/aws/` | same |
| events | `infrastructure/events/` + capability module in `infrastructure/aws/` | same |
| artifacts | `infrastructure/artifacts/` + capability module in `infrastructure/aws/` | same |
| observability | `infrastructure/observability/` + capability module in `infrastructure/aws/` | same |

不得建立 vendor-name alternate directory。`infrastructure/aws/` 是 shared physical directory；Provider 只改自身 capability module，不改其他 capability 或 Core contract。

## 3. Adapter template

每個 adapter 由以下邏輯元件構成：

1. Contract validator：schema major、required、enum/range/format、size、deadline。
2. Method policy：逐 operation 強制 `x-method-policy` 的完整 timeout、retry owner、idempotency、concurrency，不以 port-wide default 補缺。
3. Mapper：Application DTO → provider payload；canonical Decimal 不經 binary float。
4. Client boundary：bounded timeout/cancellation、zero hidden retry 或明確固定 SDK retry。
5. Response mapper：provider payload → DTO；schema 與 shared semantic assertions 驗證。
6. Error mapper：vendor failure → 該 method 的 `ErrorResult` union；unknown exception → `unexpected_provider_error`，details 只含 allowlisted safe data。
7. Redaction/metrics：只記安全 operation metadata。
8. Replay/idempotency adapter：依該 method policy 處理 duplicate/unknown outcome。

## 4. Deadline sequence

```text
receive DeadlineDTO
→ validate UTC/budget/sent time/safety margin
→ compute UTC remaining
→ sample receiver-local monotonic clock
→ local duration = min(provider limit, budget, UTC remaining - margin)
→ refuse I/O if duration <= 0
→ invoke with outer SDK/HTTP timeout
→ cancel and map timeout without background side effect
```

任何 runtime monotonic epoch 不進 payload或 log correlation。

## 5. Provider-specific boundaries

### SourceCollector

Static-first；只有 capability 宣告可動態抓取時才用 Playwright。每個 redirect 重新 allowlist/DNS/IP 驗證。Raw bytes/clean bytes、redirect、per-host rate/concurrency 由 ADR-007 proposed policy 強制。外部指令文字永遠是 data。

### EvidenceExtractor

Input 只含 bounded cleaned content/locator。Provider result 不含 Evidence/system lineage fields。Core 觸發至多一次 repair；adapter 不自行遞迴。

### MarketRegimeProvider

Provider 只做 inference。Probability semantic sum 驗證失敗回 `invalid_probability_distribution`。Timeout/unavailable/invalid output 不生成替代分布；Core fallback 不在 adapter module。

### ReasoningProvider

No-tool execution。Model role 由 command 指定；adapter 不自行切換。Output 不含 chain-of-thought；citation/numeric graph 最終由 Core validator 決定。

### Persistence/events/artifacts/observability

Resource key/index/table/bucket/queue 是 Infrastructure detail；不得回 DTO。Repository 維持 conditional/transactional semantics；event at-least-once；artifact hash/key/Manifest order；observability redaction fail-closed。

## 6. Contract verification

- Contract schema、37 stable methods 與 37 valid/37 invalid examples由 Core 擁有。
- Provider harness 可建立 client fixture、fault injection、record/replay 與 provider-specific assertions，但必須呼叫 shared assertions；shared suite 逐 operation 驗證 `x-method-policy`、method-specific `ErrorResult`/`unexpected_provider_error`，並驗證 Evidence offset/token TTL、Reasoning citation graph、Artifact Manifest 跨欄 semantics。
- Integration tests 只在 `tests/integration/providers/`，需顯式 credential/environment flag；Core-owned integration 只在 `tests/integration/core/`，Provider 不得修改。
- Evidence 必含 command、contract/provider versions、timeout/retry config、result摘要；不含 secret/raw sensitive payload。

## 7. Change flow

Port gap → `docs/proposals/PORT_CHANGE_<name>.md` → Core Maintainer SemVer/compatibility/test review → Core 更新 shared schema/assertions → Provider 更新 adapter/harness。Proposal 本身不授權改 shared contract。

## 8. Decision gates

OQ-B001～OQ-B011 依各 capability 與 Core T01/T10 的直接依賴執行；特別是 OQ-B004 schema human review、OQ-B008 collector security 與 OQ-B011 ownership。Schema validation 不等於批准。

### 8.1 Phase-specific production gates

Unresolved production policy 不阻止 Provider 先依穩定 Port 建立與其無關的 mapper、bounded client boundary、fake/harness 或 adapter skeleton。Fake behavior 必須標示 non-production，未核准前不得宣稱 production-complete，production composition root 不得接入未核准的 adapter/ruleset。

| Decision | Provider gate | 不受此 gate 阻塞 |
|---|---|---|
| OQ-B012 | PA74 production persistence adapter、DynamoDB transaction/key/index、distributed concurrency/quota/idempotency integration | event、artifact、observability、Port interface、in-memory fake |
| OQ-B013 | PA70 真實 live-extension provider、credentials/readiness、2026-05-31 後 production-complete reporting | 一般 Collector/Web Grounding、官方 CSV reader、historical analysis、fake live extension、PA72 MarketRegime inference |
| OQ-B014 | production dedup/independence ruleset 的 provider integration evidence | PA70 Collector、PA71 EvidenceExtractor、PA72 MarketRegimeProvider、PA73 ReasoningProvider 的無關工作 |
| OQ-B015 | production trust/confidence/contradiction-severity ruleset 的 provider integration evidence | PA70～PA73 無關 adapter、report DTO、renderer skeleton |

因此 OQ-B012～OQ-B015 不是 PA00 或所有 Provider tasks 的全域 gate；每項只在對應 production slice 開始前要求核准。
