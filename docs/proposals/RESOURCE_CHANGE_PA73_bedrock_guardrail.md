# RESOURCE_CHANGE: PA73 Bedrock Guardrail Provisioning

- **Status:** APPROVED / DRAFT_PROVISIONED / NOT_YET_VERSIONED
- **Resource type:** Amazon Bedrock Guardrail
- **Region:** us-west-2
- **Approved name:** pa73-reasoning-guardrail
- **Safeguard tier:** STANDARD
- **Cross-Region Guardrail profile:** `us.guardrail.v1:0`
- **Affected task:** PA73 ReasoningProvider adapter

## Non-sensitive approval record

- **Approval date:** 2026-08-02
- **Core/architecture design:** approved
- **AWS sandbox resource provisioning:** approved with bounded operations
- **Live evidence plan:** approved with bounded operations
- **Identity record:** no approver identity, UserId, ARN, or Account ID is recorded

Approval authorizes only the bounded remaining phases described here and in `LIVE_EVIDENCE_PA73_bedrock_reasoning.md`; it is not evidence that testing or live execution occurred. The Guardrail DRAFT was created in a prior authorized action, but its identifier is intentionally not recorded. No numeric Guardrail version exists, Guardrail fixture execution has not occurred, live evidence has not occurred, and PA73 remains incomplete. This blocker-fix change performs no AWS operation, credential access, provisioning, policy test, Converse call, or task-status update.

## Approved cross-region design

The real Guardrail configuration will reside in `us-west-2` and use system-defined Cross-Region Guardrail profile `us.guardrail.v1:0`. The profile supplies routing only; runtime still requires the exact Guardrail ID and an immutable numeric Guardrail version. STANDARD Prompt Attack protection requires this cross-region boundary. Before execution, human review must confirm supported US destination regions and IAM/service-control-policy coverage for each permitted destination. No real account identifier or complete resource ARN may be recorded in repository evidence.

Cross-region evaluation may process prompts and responses in profile-supported US destination regions. Cost, service availability, data geography, and authorization therefore differ from a source-region-only design and remain explicit execution-time review items.

## Approved policy design

### Prompt Attack

Use **HIGH** strength input blocking. Input intervention is fail-closed. Test fixtures must distinguish direct attacks, quoted attacks in security reporting, benign schema discussion, and ordinary CryptoTrust evidence. Detect-only may be used only in separately authorized isolated DRAFT tuning and never authorizes production detect-only behavior.

### Harmful content — provisional DRAFT settings

These settings remain provisional until the complete Guardrail fixture suite passes and human review accepts false-positive and false-negative behavior:

| Category | DRAFT setting |
|---|---|
| HATE | MEDIUM input/output blocking |
| INSULTS | LOW input/output blocking |
| SEXUAL | MEDIUM input/output blocking |
| VIOLENCE | LOW input/output blocking |
| MISCONDUCT | LOW input/output blocking, with isolated detect-only comparison during tuning |

CryptoTrust evidence may legitimately discuss exploits, fraud, sanctions, money laundering, manipulation, enforcement, and violent or criminal news. The fixtures must therefore include neutral legitimate reporting as well as disallowed generation requests. No listed harmful-content setting is final before fixture evidence and human review.

### Sensitive information

Use input/output **BLOCK** for the built-in types `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`, and `PASSWORD`. These are separate built-in types, not a generic credential category. Fixtures must be unmistakably synthetic and non-usable.

Custom regular expressions `PA73_SESSION_TOKEN`, `PA73_AUTHORIZATION_HEADER`, and `PA73_GENERIC_API_KEY` use the exact no-lookaround patterns recorded in `LIVE_EVIDENCE_PA73_bedrock_reasoning.md`; each applies to INPUT and OUTPUT with BLOCK. A custom regex is not represented as a built-in PII type.

Built-in `EMAIL` applies to INPUT and OUTPUT with ANONYMIZE so legitimate public-interest evidence can retain analytical meaning. Fixture review remains mandatory before numeric version creation. Detect-only is limited to separately authorized isolated tuning.

No raw Guardrail assessment, prompt, response, policy rationale, credential-like fixture content, or sensitive content may be retained. Evidence is limited to safe pass/reject outcomes, fixed typed codes, policy binding, and non-sensitive aggregate counts.

### Topics and word filters

Do not broadly deny crypto, hack, exploit, fraud, sanction, regulation, or related mission-critical topics. No denied topic is approved initially. Word filters cannot replace citation/reference-graph validation, strict schema validation, or bounded task-scope validation.

## Adapter and production behavior

Apply the Guardrail to model input and response. It supplements and never replaces strict JSON parsing, duplicate-key rejection, canonical Decimal handling, citation graph validation, context-boundary enforcement, no-chain-of-thought validation, deadline enforcement, or error redaction.

Every Guardrail intervention fails closed to local typed code `guardrail_rejected` with fixed message `Request rejected by safety policy.` The message must echo no input, output, policy reason, credential material, provider metadata, or assessment detail. Primary Opus and fallback Nova use the same Core policy and intervention semantics.

Production Converse trace remains `disabled`. No raw assessment is requested or retained. The model receives no orchestration, network, database, object-store, or secret tools. Guardrail behavior does not select repair/fallback workflow branches or weaken validation.

## DRAFT testing and version governance

1. The approved policy has been created as DRAFT; no identifier is recorded here.
2. Run the complete non-sensitive Guardrail policy fixture suite against DRAFT only under its separate identity and call budget.
3. Stop on any fixture failure. Do not continue to live model evidence.
4. Conduct human review before numeric version creation.
5. If tuning is required, only the bounded authorized update may modify DRAFT; rerun the complete suite and repeat review.
6. Create one immutable numeric version only after all Guardrail fixtures pass and approval is recorded.
7. Bind live execution to the exact execution-supplied Guardrail ID, exact numeric version, and Core `guardrail_policy_version`.

The fixed policy fixture is `tests/fixtures/pa73/guardrail_policy_cases_v1.json`, bound to `sha256:a8fc84bfe38189d93373faf3f7f7cd12a671bb034d73cec0b8a04ca29fd9a4e5`.

DRAFT must never be cited as PA73 live evidence. A numeric version does not currently exist. Later changes require a new reviewed numeric version; silent mutation of an approved binding is prohibited. Rollback selects a previously approved immutable version. Deletion is not authorized by this proposal.

## IAM, logging, and evidence boundaries

Provisioning, policy-test, and runtime identities remain separate and least-privileged. Wildcard Bedrock permission, IAM mutation, logging-destination creation, Guardrail deletion, and unrelated resource mutation are prohibited. Repository documents must not contain credential values, approver identity, UserId, ARN, Account ID, request ID, CloudTrail identifiers, or CloudWatch resource identifiers.

Model Invocation Logging full-text prompt/response storage is prohibited. Production evidence requires a fail-closed logging preflight and stops if text delivery is enabled or its state is unknown. Service metrics or control-plane audit events may exist, but their identifiers and payloads are not repository evidence.

Guardrail policy fixture calls are separate from both primary Opus and fallback Nova live-evidence budgets. They use separate authorization, identity, scope, accounting, and evidence. Passing adapter tests does not approve a Guardrail policy; passing Guardrail fixtures does not prove adapter schema/citation behavior.

## Current state

The design and bounded plans are approved. DRAFT is provisioned, but its identifier is intentionally not recorded; no numeric version exists, and neither the Guardrail fixture suite nor live evidence has executed. PA73 can be complete only after the Guardrail fixture suite, human review, immutable version creation, both-role live evidence, and all local validations succeed. This document does not update the PA73 checkbox.
