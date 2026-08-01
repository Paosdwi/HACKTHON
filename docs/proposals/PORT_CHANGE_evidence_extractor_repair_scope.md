# PORT_CHANGE: EvidenceExtractor Repair Authorization Scope

- **Status**: Approved with Core decisions — implementation authorized
- **Approved date**: 2026-08-01
- **Approver**: Core Maintainer
- **Proposal type**: Shared Port contract change
- **Affected Port**: `EvidenceExtractor`
- **Affected operation**: `repair`
- **Current frozen contract**: `EvidenceExtractor` / `RepairRequestDTO` 1.0.0
- **Approved target**: versioned `EvidenceExtractor` contract / `RepairRequestDTO` 2.0.0
- **Authority**: Core Contract Maintainer

> This approval authorizes a later Core-owned implementation of a new versioned 2.0.0 contract. It does not itself implement v2, does not authorize overwriting frozen 1.0.0, does not mark PA71 complete, and does not authorize a Provider to create a compatibility contract before the Core v2 contract is published and merged.

## Approval scope

This approval is limited to the contract decisions recorded in this proposal.

It authorizes subsequent Core work to create a separate EvidenceExtractor 2.0.0 contract and its Core-owned bindings, examples, assertions, and fake. It does not:

- modify, relabel, reinterpret, or retire frozen EvidenceExtractor 1.0.0;
- modify v1 schemas, examples, assertions, DTOs, Ports, or stable contract IDs;
- complete PA71 or authorize its successful-repair slice before Core v2 is published and merged;
- authorize a Provider-owned extension, shadow DTO, SDK payload, or alternate compatibility contract;
- change any task checkbox or `docs/architecture/open-questions.md` status;
- start EvidenceExtractor v2, PA71, or T63 implementation.

## Problem

The frozen `RepairRequestDTO` 1.0.0 does not carry enough Core-authoritative scope for an adapter to perform a successful repair safely.

The existing request contains:

- `original_result`;
- `validator_errors`;
- `raw_record_id` and `raw_content_hash`;
- `context_hash`;
- output schema and guardrail policy versions;
- task, execution, operation, and deadline fields.

It does **not** contain:

1. the authoritative cleaned content used for extraction, or an immutable locator from which that exact content can be resolved;
2. a hash bound to that authoritative cleaned content;
3. the Core-approved asset scope;
4. the Core-allowed event taxonomy;
5. an authority hash binding those values to one repair authorization.

`raw_content_hash` and `context_hash` already exist in 1.0.0, but neither proves which cleaned content is authoritative for quote grounding during repair. A raw-record lineage hash must not be treated as a substitute for a hash of the exact cleaned content.

`original_result` is untrusted candidate output. Its first claim, any other claim, provider metadata, or validator diagnostics cannot establish authority for content, assets, taxonomy, lineage, or repair scope. Invalid provider output cannot authorize its own repair.

Because `RepairRequestDTO` 1.0.0 has `additionalProperties: false`, an adapter cannot safely receive the missing authority through an untyped extension. Without the approved v2 contract, an adapter cannot prove that:

- every repaired quote is grounded in the authorized cleaned content;
- every repaired claim stays within the approved asset scope;
- every repaired event type belongs to the approved taxonomy;
- locator-resolved bytes are the exact content authorized by Core;
- same-operation replay uses the same repair authority.

An offline v1 quarantine-only path may remain safe because it does not claim successful repair. The repository task authority remains unchanged, and this proposal does not mark PA71 complete. The gap blocks authoritative **successful** repair until Core v2 is published and merged.

## Approved Core decisions

### 1. Contract version

The change is a **MAJOR** version change.

- The new EvidenceExtractor contract version is `2.0.0`.
- The new `RepairRequestDTO` version is `2.0.0`.
- Frozen EvidenceExtractor 1.0.0 must remain complete and unchanged.
- No v1 schema, example, assertion, field, semantic, or stable contract ID may be modified, relabeled, or reinterpreted as v2.

### 2. Required `RepairRequestDTO` v2 authority fields

The following field names are approved and required for successful repair:

| Field | Binding meaning |
|---|---|
| `content` | Core-authorized exact bounded cleaned content or immutable opaque locator. |
| `clean_content_hash` | SHA-256 of the exact authoritative cleaned-content UTF-8 bytes. |
| `assets` | Core-approved asset scope. |
| `allowed_event_taxonomy` | Core-approved event taxonomy. |
| `repair_authorization_hash` | Core-owned digest binding the versioned repair authorization payload. |

These are required v2 authority fields. An absent or invalid field cannot be treated as a successful-repair request.

### 3. Content authority

`content` reuses the existing closed `ContentInputDTO` union:

- `inline` contains the exact bounded cleaned content authorized by Core;
- `locator` contains a Core-authorized opaque locator that is immutable for that operation.

For locator input, the resolved bytes must be verified against `clean_content_hash` before provider invocation.

Content authority must not be inferred from:

- `original_result`;
- the first or any other original claim;
- `validator_errors`;
- provider output or diagnostics.

Application DTOs must not carry signed-locator credentials, temporary credentials, SDK types, or provider-specific locator payloads. Locator representation remains opaque at the Application boundary.

### 4. Hash semantics

The approved hash roles are distinct:

- `raw_content_hash` remains the raw-record lineage identity.
- `clean_content_hash` is the SHA-256 of the exact authoritative cleaned-content UTF-8 bytes.
- `context_hash` retains its existing meaning and must not be silently redefined in v2.
- `repair_authorization_hash` is added as the Core-owned repair authorization identity.

`repair_authorization_hash` is computed from versioned canonical JSON containing at least:

- `authorization_ruleset_version`;
- `raw_record_id`;
- `raw_content_hash`;
- `clean_content_hash`;
- canonical sorted `assets`;
- canonical sorted `allowed_event_taxonomy`;
- `output_schema_version`;
- `guardrail_policy_version`.

Canonicalization rules:

1. Deduplicate `assets` and `allowed_event_taxonomy`.
2. Sort each list by Unicode code point.
3. Serialize as canonical JSON encoded as UTF-8.
4. Hash with SHA-256.
5. Encode using the existing canonical hash wire: `sha256:` followed by 64 lowercase hexadecimal characters.

The authorization hash preimage must not include:

- the full inline cleaned content;
- a signed locator secret;
- temporary credentials;
- provider payload or diagnostics.

Replay semantics:

- same `operation_id` plus the same canonical authorization payload may replay the recorded result;
- same `operation_id` plus a different authority payload must fail as a payload conflict;
- an adapter or Provider must not create, replace, or reinterpret `repair_authorization_hash`.

### 5. Typed repair errors

The v2 `RepairErrorResult` adds these exact method-specific error codes:

| Error code | Required trigger and behavior |
|---|---|
| `repair_content_unavailable` | Authoritative content or locator cannot be resolved safely within the effective deadline. Fail before provider invocation. |
| `repair_content_hash_mismatch` | Inline or locator-resolved bytes do not match `clean_content_hash`. Fail before provider invocation. |
| `repair_asset_scope_violation` | Repaired output contains an asset outside Core-approved `assets`. Reject the provider output. |
| `repair_event_taxonomy_violation` | Repaired output contains an event type outside `allowed_event_taxonomy`. Reject the provider output. |

The adapter returns the typed error. Core decides quarantine, fallback, Execution degradation, and final outcome.

Error payloads and diagnostics must not contain:

- raw or cleaned content;
- signed locator or temporary credentials;
- provider request/response payload;
- validator diagnostic dumps;
- secret, token, Authorization header, stack trace, or raw vendor exception.

Existing method-specific errors remain available where applicable, including safe unknown-exception mapping to `unexpected_provider_error`.

### 6. Ownership

#### Core owns

- repair authorization;
- `repair_authorization_hash` and its ruleset/versioned canonicalization;
- content or locator selection;
- approved assets;
- allowed event taxonomy;
- repair trigger and the at-most-one-repair decision;
- quarantine, fallback, Execution degradation, and final outcome;
- Task, Execution, RawRecord, Evidence, link, assessment, and other system lineage fields;
- system IDs, timestamps, content hashes, locators, and authoritative scope;
- shared schemas, examples, assertions, DTO/Port bindings, and acceptance semantics.

#### Adapter only

- defensively validates the versioned request;
- resolves the authorized locator through its approved capability boundary;
- verifies `clean_content_hash`;
- maps verified content and approved scope to the provider;
- performs one provider invocation;
- validates exact quote grounding;
- validates asset and taxonomy scope;
- maps provider output or failure to a safe schema-valid result or typed error.

#### Provider must not create or overwrite

- Task, Execution, RawRecord, or Evidence IDs;
- system timestamps;
- lineage;
- content hashes;
- locators;
- approved assets or taxonomy;
- quarantine, fallback, Execution outcome, or publication decisions.

The adapter must not infer authority from provider output, broaden scope, create a repair loop, or introduce hidden retry.

### 7. Timeout and attempts

The aggregate repair hard timeout is at most **20,000 ms**.

The same effective repair deadline covers:

- locator resolution;
- content hash verification;
- provider invocation;
- response schema validation;
- quote, asset, taxonomy, and output validation.

Required execution policy:

- Core triggers at most one repair;
- adapter provider attempt equals 1;
- hidden retry equals 0;
- deadline exhausted before I/O prevents I/O;
- receiver rebuilds the distributed deadline using receiver-local monotonic time;
- late locator or provider results cannot re-enter a pipeline after Core quarantine.

No substep may create an independent timeout window that extends the aggregate deadline.

### 8. Contract-test identity

Contract-test identities are version-specific:

- v1 repair remains `CT-EXTRACT-REPAIR-01`;
- v2 successful repair uses `CT-EXTRACT-REPAIR-02`.

V2 examples or assertions must not replace, alias, or reinterpret `CT-EXTRACT-REPAIR-01`.

### 9. Version negotiation and compatibility

- V1 repair is limited to its existing quarantine-only behavior.
- A v1 request is insufficient proof for successful repair.
- Successful repair is enabled only when both the Core producer and Provider adapter support v2.
- Failure of v2 validation, locator resolution, or hash verification must not downgrade to v1 successful repair.
- Unknown major versions fail closed.
- V1 compatibility remains for the hackathon version; no v1 removal is authorized.
- PA71's successful-repair slice must wait until the Core v2 contract is published and merged before it can be completed.
- A Provider must not publish or rely on a private compatibility contract while waiting for Core v2.

### 10. Shared contract requirements

A later Core-owned v2 implementation must add independent versioned artifacts for:

- machine-readable schema;
- valid examples;
- invalid examples;
- Core-owned shared assertions;
- DTO/Port binding;
- Core fake;
- provider harness integration instructions.

The v2 contract suite must cover:

- inline and locator successful repair;
- exact quote grounding;
- content hash match and mismatch;
- unavailable locator;
- approved and unapproved assets;
- allowed and unknown taxonomy;
- original first-claim non-authority;
- injection resistance and redaction;
- aggregate timeout at most 20 seconds;
- at most one Core-triggered repair;
- exactly one adapter provider attempt;
- zero hidden retry;
- same-operation replay and payload conflict;
- v1 quarantine-only compatibility;
- unknown-major fail-closed behavior.

Only Core may create or update these shared artifacts. Provider harnesses may consume the published v2 assertions but may not define or modify their expected semantics.

## Compatibility impact

### Existing 1.0.0 consumers

Known or expected consumers of `RepairRequestDTO` 1.0.0 include:

- the Core Application/orchestrator request producer;
- Application DTO serialization and validation;
- Core fake EvidenceExtractor implementations;
- provider adapter request mappers;
- frozen schema validators and valid/invalid examples;
- Core-owned shared contract assertions and provider harnesses;
- provider integration fixtures and recorded replay data;
- operation idempotency and payload-hash records.

Making the approved authority fields required would invalidate existing v1 payloads. Changing `context_hash`, `raw_content_hash`, or v1 error semantics in place would also be breaking. Existing v1 consumers reject unknown fields because v1 uses `additionalProperties: false`.

### Approved migration boundary

1. Keep every frozen 1.0.0 artifact and semantic unchanged.
2. Core publishes separate v2 schemas, DTO/Port bindings, examples, shared assertions, and fake.
3. Core and adapters explicitly declare supported major versions; unknown majors fail closed.
4. V1 quarantine-only behavior remains available during the hackathon compatibility window.
5. V1 never proves successful repair.
6. Enable successful repair only after both the Core producer and Provider adapter support published v2.
7. Do not downgrade failed v2 authorization or integrity validation to v1 successful repair.
8. Migrate Provider mappers, harnesses, fixtures, and replay identity only after Core v2 is published and merged.
9. Do not remove v1 during the hackathon version.
10. Do not dual-write ambiguous authority fields or create adapter-private extensions.

## SemVer impact

**Approved: MAJOR, target 2.0.0.**

The new authority fields are required for successful repair, causing payloads valid under 1.0.0 to be insufficient under the successful-repair contract. V2 also adds method-specific error semantics and a new authority-bound replay identity. These are breaking contract changes and require a new major version.

A minor release with optional authority fields is not authorized because it would preserve a successful-repair path without provable grounding or scope.

Frozen 1.0.0 must not be overwritten, relabeled, reinterpreted, or removed by this approval.

## Testing requirements

The later Core v2 implementation and shared suite must satisfy all approved testing requirements below.

### Successful repair and grounding

- Successful repair with inline exact cleaned content and matching `clean_content_hash`.
- Successful repair with an immutable locator whose resolved bytes match `clean_content_hash`.
- Every repaired quote is an exact substring of authoritative cleaned content.
- A quote present only in `original_result`, provider diagnostics, or another record is rejected.
- A misleading first claim cannot alter content, asset, taxonomy, or repair authority.

### Asset authorization

- Claims containing only approved assets are accepted.
- Any unapproved asset returns `repair_asset_scope_violation`; Core quarantines or applies its approved fallback/outcome policy.
- Empty, duplicate before canonicalization, malformed, or oversized asset inputs are handled according to schema and canonicalization rules.
- Provider output cannot broaden the approved asset set.

### Event taxonomy authorization

- Allowed event types are accepted.
- Unknown event types return `repair_event_taxonomy_violation`; Core decides quarantine/fallback/outcome.
- Empty, malformed, or oversized taxonomy is rejected; duplicate values canonicalize deterministically for the authorization hash and cannot create a second authority identity.
- Original claims cannot authorize an event type.

### Content integrity and availability

- Inline hash mismatch fails before provider invocation.
- Locator hash mismatch fails before provider invocation.
- Missing, expired, denied, unsafe, or unavailable locator returns `repair_content_unavailable` without provider invocation.
- Locator resolution, hashing, provider invocation, and all validation share one aggregate deadline.
- Unicode and line-ending fixtures prove hashing uses exact UTF-8 bytes without implicit normalization.
- `repair_authorization_hash` golden vectors cover sorted assets/taxonomy and excluded sensitive fields.

### Security, injection, and redaction

- Prompt-injection text is treated only as untrusted content and cannot alter scope, schema, policy, tools, or orchestration.
- Provider attempts to emit system lineage fields never become Core authority.
- Unknown exceptions map to `unexpected_provider_error` without raw exception text.
- Logs/events exclude full content, full prompt, token, Authorization header, signed locator, temporary credential, PII, SDK dump, provider payload, stack trace, and validator diagnostic dump.
- Redaction failure fails closed and never falls back to raw logging.
- Safe diagnostics are allowlisted and bounded.

### Deadline, attempts, replay, and quarantine

- Aggregate repair hard timeout is at most 20,000 ms.
- Core triggers repair at most once.
- Adapter provider invocation count is exactly one when pre-invocation authority checks pass.
- Hidden retry count is zero.
- Expired deadline prevents locator and provider I/O.
- Late locator/provider results cannot re-enter a quarantined pipeline.
- Same operation and same canonical authorization payload replay consistently.
- Same operation with changed content hash, assets, taxonomy, ruleset, schema, or policy fails as payload conflict.
- Typed repair errors do not let invalid repaired claims enter Evidence, Reasoning Context, or publication.

### Version compatibility

- Frozen v1 schemas, examples, IDs, and assertions pass unchanged.
- V1 quarantine-only behavior remains compatible.
- V1 cannot produce authoritative successful repair.
- `CT-EXTRACT-REPAIR-01` remains v1-only.
- `CT-EXTRACT-REPAIR-02` covers v2 successful repair.
- Unknown major versions fail closed.
- V2 failures cannot downgrade to v1 successful repair.

## Core v2 work authorized but not implemented by this approval

A future, separately scoped Core implementation must create and validate:

1. an independent EvidenceExtractor 2.0.0 machine-readable schema;
2. independent v2 valid and invalid examples;
3. `CT-EXTRACT-REPAIR-02` Core-owned shared assertions;
4. v2 DTO/Port binding without changing v1 bindings;
5. canonical `repair_authorization_hash` construction and golden vectors;
6. a Core fake implementing v2 success, typed failures, replay, conflict, deadline, and late-result isolation;
7. version negotiation and unknown-major fail-closed behavior;
8. provider harness integration instructions that consume, but do not redefine, Core semantics;
9. compatibility evidence proving v1 quarantine-only behavior remains unchanged.

No item in this list is implemented by editing or approving this proposal.

## Rejected alternatives

### Infer authority from `original_result`

Rejected. Provider output is the object being repaired and cannot authorize itself. The first claim cannot establish content, asset, taxonomy, or lineage authority.

### Reuse `raw_content_hash` as cleaned-content proof

Rejected. Raw-record lineage and exact cleaned bytes are distinct concerns.

### Redefine `context_hash`

Rejected. V2 retains its existing meaning and uses the separate `repair_authorization_hash` for repair authority.

### Add optional fields in a minor release

Rejected. Optional fields preserve the successful-repair security gap.

### Use adapter configuration or provider-specific extensions

Rejected. This would violate the closed contract and move Core authorization into Provider ownership.

### Let the adapter broaden scope after inspecting output

Rejected. The adapter validates and maps output; it does not define authority or outcome.

### Reuse the v1 contract-test identity

Rejected. V2 successful repair uses `CT-EXTRACT-REPAIR-02`; v1 remains `CT-EXTRACT-REPAIR-01`.

## Resolved Core Maintainer decisions

The former open decisions are resolved as follows:

1. Required field names are `content`, `clean_content_hash`, `assets`, `allowed_event_taxonomy`, and `repair_authorization_hash`.
2. Content uses the existing closed inline/locator union; inline is exact bounded cleaned content and locator is Core-authorized, opaque, and operation-immutable.
3. `context_hash` is not redefined; `repair_authorization_hash` carries the new authority identity.
4. Typed error names are fixed to the four codes listed in this proposal.
5. Authorization hashing uses versioned UTF-8 canonical JSON with deduplicated Unicode-code-point-sorted assets/taxonomy and canonical lowercase SHA-256 wire encoding.
6. V1 remains quarantine-only during the hackathon compatibility window; unknown majors fail closed; no failed v2 request downgrades to successful v1 repair.
7. V1 retains `CT-EXTRACT-REPAIR-01`; v2 successful repair uses `CT-EXTRACT-REPAIR-02`.

No implementation ambiguity may be resolved by altering v1 or shifting authorization ownership to Provider code.

## Non-goals and implementation guard

This approval does not:

- itself modify any contract, DTO, Port, schema, example, shared assertion, implementation, fake, harness, or test;
- modify or complete PA71;
- authorize PA71 successful repair before Core v2 is published and merged;
- authorize Provider-created compatibility semantics;
- modify any task checkbox or open-question status;
- select a provider, model, SDK, storage service, or locator technology;
- authorize provider-created IDs, timestamps, lineage, hashes, locators, approved scope, quarantine, fallback, outcome, or publication;
- start T63;
- authorize a commit, push, pull request, deployment, or v1 retirement.
