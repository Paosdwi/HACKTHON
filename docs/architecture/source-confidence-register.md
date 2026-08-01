# Source Confidence Register

## Source policy

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。

`confidence` 只表示來源是否足以支持該列敘述，不表示 ADR 已核准。`confirmed` 可來自本輪使用者明示方向，或 Master 可直接確認的 FR、英文 schema/JSON、數值、AWS 元件、路徑與驗收；`inferred` 是設計推導；`uncertain` 不得作為實作依據。所有非唯一來源支持的設計值均是 **Maintainer Proposed Default**。

## Requirement confidence

| ID | source | confidence | reliable substance / boundary | blocking | required confirmation |
|---|---|---|---|---|---|
| CP-FR001-* | Master FR-001、Task API JSON、17.4 | confirmed | Task input、JWT-derived identity、24h reuse、10/hour、422/429、Execution timing 可直接確認；fingerprint/claim 細節非唯一 | yes | ADR-001/002 owners approve before T10 |
| CP-FR002-* | Master FR-002、`source_requirement_matrix` JSON | confirmed | 三題型、source classification、Planner authority | no | Schema mapping review |
| CP-FR003-* | Master FR-003、Execution Log JSON | confirmed | source categories、terminal outcomes、安全記錄欄位 | yes | Provider list/collector policy approval |
| CP-FR004-* | Master FR-004、15.6、17.2 | confirmed | static-first、Playwright、lineage、SSRF control categories；精確 limits 非唯一 | yes | ADR-007 approval |
| CP-FR005-* | Master FR-005、Evidence/Link/Assessment JSON | confirmed | immutable Evidence、link/assessment separation、latest use；locator/sequence/page 是設計推導 | yes | ADR-005 approval |
| CP-FR006-* | Master FR-006 | confirmed | dedup dimensions、independence group、ruleset version；threshold/window/allowlist 未定 | yes | OQ-B014 decision |
| CP-FR007-* | Master FR-007、market-regime JSON、4.2–4.4、7.2 | confirmed | Decimal-safe obligation、dataset numbers、25s/no-retry；wire grammar/live provider 非唯一 | yes | ADR-004 + OQ-B013 approval |
| CP-FR008-* | Master FR-008 | confirmed | score dimensions、conflict taxonomy、confidence effect；formula/threshold 未定 | yes | OQ-B015 decision |
| CP-FR009-* | Master FR-009、7.3、16 | confirmed | citation graph、60s one repair、fallback、invalid rejection；Context bounds 是推導 | yes | ADR-008 approval |
| CP-FR010-* | Master FR-010、11、12、16、17.4 | confirmed | normal formats、Manifest metadata、renderer degradation；degraded minimum 是推導 | yes | ADR-009 approval |
| CP-FR011-* | Master FR-011、14、17.4 | confirmed | Pre-flight/no quota、3/min、quota scope、2 executions、900s；TTL/binding/atomic consume 是推導 | yes | ADR-006 + OQ-B012 approval |
| CP-PORT-* | Core requirements + complete Draft 2020-12 schemas | inferred | Master 支持 adapter boundary，但 37 method signatures 不是 Master 唯一契約；11 schemas、37 valid、37 invalid 已驗證；移除 `TaskRepository.lock_for_execution` 是避免禁止的分步交易之 breaking draft change | yes | Maintainer human schema review completed；OQ-B004=`approved` |
| CP-ARCH-* | Master 8.2、19 + Core requirements | inferred | Clean Architecture 方向可確認；完整 ownership/version/change flow 是設計推導 | yes | Architecture/ADR-010 review |

## Decision confidence

| ID | source | confidence | rationale | blocking | approval status |
|---|---|---|---|---|---|
| ADR-001 | Master FR-001 + design proposal | inferred | Fingerprint obligation可確認；NFKC、whitespace、asset/time canonicalization、RFC8785/SHA-256、ruleset 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-002 | Master FR-001/011 + design proposal | inferred | JWT-derived identity/override prohibition可確認；`sub`、groups、HMAC pseudonym 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-003-WIRE | Current user direction + Master 900s/monotonic obligation | confirmed | 六欄 wire、跨 process 不比較 monotonic epoch、receiver-local monotonic、outer guards 是明示方向 | no | Approved — 2026-08-01 |
| ADR-003-SAFETY | ADR-003 design proposal | inferred | safety margin default/min/max `1000/100/5000 ms` 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-004 | Master FR-007 + design proposal | inferred | Decimal-safe obligation可確認；canonical grammar、38/18、ranges、sum tolerance 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-005 | Master FR-005/JSON + design proposal | inferred | Evidence lineage/history可確認；`raw_locator`、ContentReference、sequence/snapshot/idempotency 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-006 | Master FR-011 + design proposal | inferred | Pre-flight 不耗 quota/先於 Execution 可確認；60s、binding、single-use、atomic consume 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-007 | Master FR-004/security controls + design proposal | inferred | Control categories可確認；HTTPS/limits/rate/robots精確值是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-008 | Master FR-009 + design proposal | inferred | bounded/task-scoped/no-secret/no-raw-HTML 可確認；bytes/tokens/count/truncation 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-009 | Master FR-010/016 + design proposal | inferred | normal formats可確認；degraded minimum/publication gate/Manifest details 是 Proposed Default | no | Approved — 2026-08-01 |
| ADR-010 | Current user direction | confirmed | 精確 ownership、Core-only shared contract approval、Provider harness、proposal flow、provider task authority 是明示方向 | no | Approved — 2026-08-01 |
| PRODUCT-MISSION | Master Chinese narrative | uncertain | 若只由不可可靠判讀敘述延伸，不得形成新契約；`SOURCE_TEXT_UNCERTAIN` | no | Product owner confirmation |
| EXISTING-CODE-BASELINE | Master section 18 vs workspace snapshot | uncertain | Master 稱有程式/測試，但 workspace 只有空分層目錄；`SOURCE_TEXT_UNCERTAIN` | yes | Maintainer confirms pending import |

## Use rules

1. `uncertain` 不得新增 requirement、Port 或 capability claim。
2. `inferred` 保持 **Maintainer Proposed Default**，直到列名 owner 人工核准。
3. `confirmed` 表示來源信心，不會自動使 ADR Approved；ADR-001..010 已由 Maintainer 於 2026-08-01 另行人工核准。
4. Schema/meta-schema/examples 驗證加上 Maintainer 人工 review 後，OQ-B004 已於 2026-08-01 核准；未來 schema 變更仍須依 SemVer 與 review 流程。
5. 亂碼或不可可靠判讀片段單獨推導的新敘述必須新增 `SOURCE_TEXT_UNCERTAIN` 與 Open Question，不猜測原意。
