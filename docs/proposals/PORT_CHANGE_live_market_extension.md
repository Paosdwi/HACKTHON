# PORT_CHANGE: Live Market Extension Contract

- **Status**: Approved — implementation authorized
- **Approved date**: 2026-08-02
- **Approver**: Project owner / Core Maintainer
- **Proposal type**: Shared Port contract addition
- **Affected capability**: Post-2026-05-31 daily OHLCV live extension
- **Decision gate**: OQ-B013
- **Proposed provider implementation**: Binance Spot public market-data API
- **Proposed contract**: `LiveMarketDataProvider` 1.0.0
- **Owners**: Core Contract Maintainer (Port/DTO/policy) and Provider Adapter Maintainer (HTTP implementation)

> Human approval was recorded on 2026-08-02 for OQ-B013, Binance Spot, and the recommended defaults. This authorizes the separately versioned Core contract and Provider implementation described here; it does not permit modification of pre-existing frozen contracts.

## Problem

The official hackathon OHLCV dataset ends on 2026-05-31. The Master Spec requires every reporting range after that date to use a qualified live extension with the same daily `date, open, high, low, close, volume` shape, UTC boundaries, USDT quote, Decimal-safe parsing, point-level provenance, and an explicit `transition_date`.

Core currently provides `FakeLiveMarketExtension`, but there is no Application-owned live-market Port or versioned request/result/error DTO. `SourceCollector` is not sufficient because it returns generic collected records and does not define:

- the supported asset/pair/day interval;
- closed-candle and UTC-day semantics;
- canonical Decimal OHLCV mapping;
- pagination and coverage semantics;
- overlap reconciliation with the official dataset;
- missing/duplicate/out-of-order day behavior;
- live-provider readiness and typed failures;
- deterministic provenance required by `MarketBar` and the final artifacts.

Implementing an adapter directly against `FakeLiveMarketExtension` or a Provider-owned Protocol would create a shadow Core contract and would not integrate safely into the formal-run pipeline.

## Proposed provider

Use the Binance Spot public market-data endpoint:

- Base URL: `https://api.binance.com`
- Operation: `GET /api/v3/klines`
- Symbols: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `XRPUSDT`
- Interval: `1d`
- Time zone: `0` (UTC)
- Pagination: `startTime`, `endTime`, `limit` with a maximum of 1,000 rows
- Authentication: none for this public market-data operation

The provider response supplies open time, open, high, low, close, base-asset volume, and close time. Numeric strings must be parsed directly as `Decimal`; they must never pass through binary floating point.

Only closed daily candles are eligible. A candle whose close time is at or after the request's authoritative `as_of` instant is excluded and reported as an incomplete-day limitation, never treated as final market evidence.

This provider choice does not make Binance authoritative over the official dataset. Official rows through 2026-05-31 remain immutable and win every overlap conflict.

## Proposed Core contract

### Port

Add an Application-owned `LiveMarketDataProvider` Protocol, version `1.0.0`, with these methods:

1. `fetch_daily_ohlcv(request: LiveMarketDataRequestDTO) -> LiveMarketDataResultDTO | LiveMarketDataErrorDTO`
2. `health_check(request: LiveMarketHealthRequestDTO) -> LiveMarketHealthResultDTO | LiveMarketDataErrorDTO`
3. `capabilities(request: LiveMarketCapabilitiesRequestDTO) -> LiveMarketCapabilitiesResultDTO | LiveMarketDataErrorDTO`

The Application depends only on this Port and DTOs. The provider implementation belongs under `infrastructure/collectors` (or another subsequently approved Provider-owned live-market module) and must not leak HTTP-client, Boto3, exchange-SDK, or vendor response types.

### Fetch request DTO

`LiveMarketDataRequestDTO` 1.0.0 contains:

| Field | Required semantics |
|---|---|
| `schema_version` | Fixed to `1.0.0`. |
| `operation_id` | Core-owned idempotency/replay identifier. |
| `asset` | Closed enum: `BTC`, `ETH`, `SOL`, `BNB`, `XRP`. |
| `pair` | Must equal `<asset>USDT`. |
| `interval` | Fixed to `1d`. |
| `start_date` | First requested UTC calendar date, inclusive. |
| `end_date` | Last requested UTC calendar date, inclusive. |
| `as_of` | Core-authoritative UTC instant used to exclude incomplete candles. |
| `deadline` | Existing distributed `DeadlineDTO`; receiver rebuilds a local deadline. |
| `expected_provider` | Fixed to the approved provider/ruleset identifier for production composition. |
| `provider_ruleset_version` | Fixed version of symbol, field, close-candle, and pagination mapping rules. |

The requested range must begin no earlier than the approved overlap-validation window preceding 2026-05-31 and must not exceed 400 UTC days per Core call. Core splits longer needs explicitly; the adapter must not create hidden unbounded work.

### Bar DTO

Each `LiveMarketBarDTO` contains:

- `schema_version`;
- `asset` and `pair`;
- `date` (UTC calendar date);
- canonical Decimal wire strings for `open`, `high`, `low`, `close`, and base-asset `volume`;
- `interval = 1d`;
- `provider` and `provider_ruleset_version`;
- `source_url` without credentials or sensitive query material;
- `fetched_at` in UTC;
- `source_open_time_ms` and `source_close_time_ms`;
- `content_hash` over the canonical mapped bar payload;
- `provenance = live_extension`;
- `schema_version`.

Core, not the adapter, creates Domain IDs and final Evidence/Analysis lineage. The adapter may return deterministic source metadata but must not create Task, Execution, Evidence, Analysis, or artifact IDs.

### Fetch result DTO

`LiveMarketDataResultDTO` contains:

- ordered, unique `bars`;
- requested and returned date bounds;
- `complete` boolean;
- explicit `missing_dates`;
- excluded `incomplete_dates`;
- `provider` and mapping ruleset version;
- request/response-safe provenance hashes;
- `fetched_at`;
- `schema_version`.

An empty or partial successful result is never silently converted to complete. Core decides whether missing required coverage makes the execution `partial` or failed.

### Health and capabilities DTOs

`health_check` performs only a bounded public connectivity/schema probe; it must not fetch an unbounded history. It reports provider reachability, server-time availability, required-symbol readiness, UTC/1d mapping readiness, and the ruleset version.

`capabilities` is deterministic and performs no network I/O. It declares supported assets, pairs, interval, quote unit, timezone, maximum page size, authentication mode (`none`), provider identity, and schema/ruleset versions.

## Validation and reconciliation

Core owns the merge of official and live bars.

1. Official data through 2026-05-31 is never overwritten.
2. Live rows begin at 2026-06-01 for reportable extension data.
3. Readiness also requests an approved overlap window ending 2026-05-31.
4. Overlap dates are compared field by field using an approved Decimal tolerance policy.
5. A reconciliation failure makes the live source not ready; it must not alter official rows.
6. Live dates must be strictly ordered and unique.
7. Missing dates remain explicit gaps; no forward-fill, interpolation, or reuse of the last official close is permitted.
8. OHLCV invariants apply: nonnegative values, `high >= open/close/low`, and `low <= open/close/high`.
9. Quote asset is always USDT and volume remains base-asset interval volume.
10. Final Report and Evidence List retain point-level `official_dataset` or `live_extension` provenance and disclose `transition_date = 2026-05-31` when live data is used.

The approved `live-market-reconciliation-1.0.0` Core ruleset uses a three-day overlap, 1% relative tolerance for OHLC fields, and 25% relative tolerance for base-asset volume. Decimal strings are parsed directly, normalized only to ADR-004 canonical wire form, and never converted through binary floating point. The adapter does not own or alter this policy.

## Errors

The v1 contract defines a closed typed error union:

| Code | Meaning | Retryable by Core |
|---|---|---|
| `invalid_request` | Unsupported asset/pair/range/interval or invalid DTO. | no |
| `deadline_exceeded` | Effective deadline expired before a complete bounded attempt. | no |
| `provider_timeout` | Public endpoint did not complete inside the effective timeout. | policy-dependent |
| `provider_rate_limited` | HTTP 429 or equivalent safe rate-limit signal. | yes, only if deadline/policy permit |
| `provider_unavailable` | DNS/connectivity/5xx/service unavailable. | policy-dependent |
| `provider_access_denied` | Geographic, policy, or endpoint access denial. | no |
| `invalid_provider_schema` | Malformed/unexpected response shape or type. | no |
| `invalid_market_bar` | Decimal, timestamp, symbol, duplicate/order, or OHLCV invariant failure. | no |
| `coverage_gap` | Provider omitted required closed UTC days. | no hidden fill; Core degrades |
| `reconciliation_failed` | Approved official-overlap tolerance was exceeded. | no |
| `payload_too_large` | Response exceeds the approved byte/row bounds. | no |
| `unexpected_provider_error` | Safe mapping of an unknown provider/client error. | no |

Error details are allowlisted. They must not include response bodies, headers, query credentials, stack traces, secrets, tokens, or raw vendor exceptions.

## Timeout, retry, and idempotency

- Proposed aggregate adapter timeout: at most 10 seconds per HTTP page and at most 30 seconds per Core operation, bounded further by `DeadlineDTO`.
- Provider attempt: one attempt per page.
- Hidden adapter retry: zero.
- Retry owner: Core.
- Rate-limit backoff, if Core approves it, must respect `Retry-After`, the remaining distributed deadline, and the maximum operation attempt policy.
- Same valid `operation_id` plus identical canonical request returns the recorded terminal result where replay storage is available.
- Same `operation_id` with a different canonical request returns `payload_conflict`.
- GET transport is naturally non-mutating, but this does not replace Core replay semantics.
- Late results cannot re-enter an expired orchestration.

## Security and deployment

- The base URL and host are fixed by approved configuration; user input cannot select a host or URL.
- HTTPS on port 443 is mandatory.
- Redirects are disabled or each hop is revalidated against the exact approved host policy.
- Private, loopback, link-local, reserved, multicast, metadata, and unsafe resolved addresses are rejected.
- Response byte and row limits are enforced before parsing or accumulation.
- No API key is required for the proposed endpoint. No placeholder secret may be hardcoded.
- AWS deployment uses the workload IAM role for AWS resources and controlled outbound HTTPS for this public provider.
- Live integration remains opt-in in tests and performs no trading or account operation.
- The adapter must never call Binance trade, account, order, withdrawal, wallet, or user-data APIs.

## Ownership

### Core owns

- Port, DTO, schema, method policy, SemVer, contract IDs, shared assertions, and fake;
- requested asset/range, deadline, replay identity, and retry decisions;
- official/live reconciliation rules and tolerance version;
- Domain mapping, merge, gaps/limitations, transition date, execution outcome, and artifact provenance;
- provider selection in the production composition root after approval.

### Provider adapter owns

- fixed-host HTTP transport;
- Binance symbol/query mapping and bounded pagination;
- exact response-shape and Decimal parsing;
- closed-candle filtering and safe source metadata;
- typed error mapping and observable, redacted events;
- implementation of the Core-published shared assertions.

The Provider must not define a shadow Protocol/DTO, change official data, fill gaps, choose fallback values, or create Core lineage IDs.

## Contract and testing requirements

Core must add independent versioned schema artifacts, valid/invalid examples, a fake, Python DTO/Port bindings, and reusable shared assertions. Proposed stable contract IDs:

- `CT-LIVE-MARKET-FETCH-01`
- `CT-LIVE-MARKET-HEALTH-01`
- `CT-LIVE-MARKET-CAPABILITIES-01`

Required tests include:

- BTC/ETH/SOL/BNB/XRP to their exact USDT symbols;
- 1d UTC request mapping and inclusive date boundaries;
- direct Decimal-string parsing with no float conversion;
- open/high/low/close/base-volume field mapping;
- pagination at the 1,000-row boundary without duplicates or gaps;
- ordered and unique bars;
- malformed tuple length/type, invalid Decimal, negative values, and invalid OHLC;
- incomplete current candle exclusion using authoritative `as_of`;
- missing day, duplicate day, out-of-range day, wrong symbol/quote, and wrong UTC boundary;
- response byte/row bounds;
- 429, 5xx, timeout, DNS, access denial, redirect, and unknown exception mapping;
- deadline exhausted before I/O and late-result rejection;
- one transport attempt, zero hidden retry, and Core-owned retry;
- same-operation replay and payload conflict;
- deterministic `capabilities` without I/O;
- bounded `health_check` without model calls or paid AWS writes;
- overlap within tolerance and reconciliation failure;
- official rows remain unchanged;
- gaps remain limitations and are never forward-filled;
- point-level provenance and transition-date artifact consistency;
- logs and events contain no response body, secret, token, raw exception, or unsafe URL material;
- live tests are opt-in; default contract/integration tests use fixtures/stubs.

## Compatibility and SemVer

This is a new Port rather than an in-place change to a frozen Port, so its first published version can be `1.0.0`. Existing official dataset and fake extension behavior remain compatible. Production composition beyond 2026-05-31 stays unavailable until Core publishes this contract, the Provider implements it, OQ-B013 decisions are approved, and the integration tests pass.

No existing frozen schema, example, assertion, or DTO is modified by this proposal.

## OQ-B013 approved decisions

The human approver accepted these exact decisions on 2026-08-02:

1. Approve Binance Spot `GET /api/v3/klines` as the primary live extension provider for the hackathon.
2. Approve public/no-credential access as the credential mode, with readiness proving that no credential is required.
3. Approve the five USDT symbols and daily UTC closed-candle semantics above.
4. Approve the Application Port/DTO/error/method-policy ownership boundary.
5. Use a three-day official-overlap window.
6. Use 1% OHLC and 25% base-volume relative tolerances under `live-market-reconciliation-1.0.0`; preserve Decimal input without float conversion.
7. Approve 10-second per-page and 30-second aggregate timeouts, one transport attempt per page, zero hidden retries, and Core-owned retry.
8. Geographic access denial fails pre-flight. A secondary provider requires a separate versioned proposal and ruleset.

Production-complete status still requires the published contract, Provider adapter, shared assertions, integration tests, and deployment readiness to pass. Approval alone does not claim those implementation results.
