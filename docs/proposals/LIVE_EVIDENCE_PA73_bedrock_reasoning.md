# LIVE_EVIDENCE: PA73 Bedrock Reasoning

- **Status:** APPROVED / NOT_YET_EXECUTED
- **Approval date:** 2026-08-02
- **Purpose:** bounded Guardrail provisioning/fixture plan and both-role ReasoningProvider live evidence
- **Current PA73 state:** incomplete; this plan does not update task status

## Fixed environment and bindings

| Setting | Approved value |
|---|---|
| AWS region | `us-west-2` |
| Credential mode | AWS profile |
| AWS profile | `default` |
| Primary base model | `anthropic.claude-opus-4-8` |
| Primary inference profile | `us.anthropic.claude-opus-4-8` |
| Fallback base model | `amazon.nova-2-lite-v1:0` |
| Fallback inference profile | `us.amazon.nova-2-lite-v1:0` |
| Guardrail name | `pa73-reasoning-guardrail` |
| Cross-Region Guardrail profile | `us.guardrail.v1:0` |
| Guardrail ID | DRAFT exists; resource identifier intentionally not recorded in repository evidence |
| Guardrail policy-test version | `DRAFT` only |
| Guardrail numeric version | not created |
| Core Guardrail policy version | `reasoning-guardrail-1.0.0` |
| Model max tokens | `1024` |
| Model temperature | `0.0` |
| Reasoning fixture path | `tests/fixtures/pa73/live_reasoning_context_v1.json` |
| Reasoning fixture SHA-256 | `sha256:e6d12c40c6990355460a13a0ad951528191be76fcec5c71a32e8c28b77de129a` |
| Guardrail policy fixture path | `tests/fixtures/pa73/guardrail_policy_cases_v1.json` |
| Guardrail policy fixture SHA-256 | `sha256:a8fc84bfe38189d93373faf3f7f7cd12a671bb034d73cec0b8a04ca29fd9a4e5` |

The digest is the computed binding for the authorized plan record; it is not claimed as independently approved evidence. The fixture is fixed, synthetic, non-production, and shared by both model roles. The runner creates separate formal role-specific requests.

## Guardrail provisioning and policy-fixture budget

This budget is separate from all model live-evidence operations:

- maximum 1 `CreateGuardrail`;
- maximum 1 `UpdateGuardrail`, usable only for the bounded approved DRAFT tuning cycle;
- maximum 1 `CreateGuardrailVersion`;
- at most 12 `ApplyGuardrail` calls total, exactly once per fixed case, in fixture order;
- zero retry and zero hidden retry for every operation;
- any mismatch, malformed response, exception, or failed expected-action comparison stops immediately and prevents every later policy case;
- human review is mandatory after passing fixtures and before numeric version creation.

The DRAFT Guardrail was created in a prior authorized action. Its resource identifier is intentionally omitted from repository evidence. The creation allowance is therefore consumed; this document does not claim policy-fixture execution, numeric-version creation, or live-model execution.

### Fixed sensitive-information policy

The three custom expressions are fixed as literal regex text, use no lookaround, apply to both `INPUT` and `OUTPUT`, and use `BLOCK`:

```text
PA73_SESSION_TOKEN:(AWS_SESSION_TOKEN|SESSION_TOKEN)\\s*[:=]\\s*[A-Za-z0-9/+=._-]{16,}
PA73_AUTHORIZATION_HEADER:Authorization\\s*:\\s*(Bearer|Basic)\\s+[A-Za-z0-9._~+/=-]{8,}
PA73_GENERIC_API_KEY:(API_KEY|api_key|x-api-key)\\s*[:=]\\s*[A-Za-z0-9._-]{16,}
```

Built-in `EMAIL` applies to both `INPUT` and `OUTPUT` with `ANONYMIZE`. Built-ins `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`, and `PASSWORD` apply to both `INPUT` and `OUTPUT` with `BLOCK`. Fixture canaries are structurally detector-compatible but synthetic and unusable; recognizable values are stored only as bounded parts and joined ephemerally in memory for one ApplyGuardrail request. Runtime content, matches, assessments, and outputs are never logged or summarized.

The Guardrail policy runner has its own explicit gate, identity/version binding, fixture hash, and 12-call budget; these are separate from the Nova/Converse gate, identity, and operation budget. The fixed cases cover Prompt Attack, harmful-content false-positive/false-negative cases, built-in credential types, custom-regex structurally non-usable synthetic canaries, ordinary synthetic PII, legitimate CryptoTrust reporting, and input/output intervention. Only `passed`, `failed`, `executed`, `expected`, policy version, fixture schema, and fixture hash may be retained. No case ID, raw content, assessment, match, output text, provider metadata, request ID, resource identifier, credential, or full exception may be retained.

A numeric Guardrail version may be created only after the complete fixture suite passes and human review accepts the DRAFT policy. The exact non-repository execution binding, numeric version, and Core policy version must then be supplied for live evidence. DRAFT exists; Guardrail fixture execution has not occurred, live evidence has not occurred, and no numeric version exists.

## Model live-evidence budget

After Guardrail success, review, and version binding, the runner may attempt:

- maximum 1 `GetModelInvocationLoggingConfiguration` total;
- primary: maximum 1 `GetFoundationModel`, 1 `GetFoundationModelAvailability`, 1 `GetInferenceProfile`, and 1 `Converse`;
- fallback: maximum 1 `GetFoundationModel`, 1 `GetFoundationModelAvailability`, 1 `GetInferenceProfile`, and 1 `Converse`.

Every operation has zero retry and zero hidden retry. No repair is permitted. No adapter-selected fallback is permitted. Any failure, unhealthy response, Guardrail intervention, invalid/schema/citation result, timeout, logging uncertainty, or late result stops every subsequent operation. No deadline extension is permitted. No resource mutation other than the separately bounded Guardrail provisioning phase is permitted.

Guardrail policy fixture calls are not Nova calls and do not consume the fallback Nova live budget. Conversely, primary/fallback model operations do not consume or extend the 12-call Guardrail fixture budget.

## Required local runner configuration

The explicit runner gate is `PA73_BEDROCK_LIVE_INTEGRATION=1`. When disabled, it is the first and only environment read. After enablement, all of the following are mandatory and exact where fixed:

- `PA73_AWS_REGION=us-west-2`
- `PA73_AWS_PROFILE=default`
- `PA73_AWS_CREDENTIAL_MODE=profile`
- `PA73_PRIMARY_BASE_MODEL_ID=anthropic.claude-opus-4-8`
- `PA73_PRIMARY_PROFILE_MODEL_ID=us.anthropic.claude-opus-4-8`
- `PA73_FALLBACK_BASE_MODEL_ID=amazon.nova-2-lite-v1:0`
- `PA73_FALLBACK_PROFILE_MODEL_ID=us.amazon.nova-2-lite-v1:0`
- `PA73_GUARDRAIL_ID=<provisioned non-ARN identifier matching ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$>`
- `PA73_GUARDRAIL_VERSION=<approved numeric version>`
- `PA73_GUARDRAIL_POLICY_VERSION=reasoning-guardrail-1.0.0`
- `PA73_APPROVED_FIXTURE_SHA256=sha256:e6d12c40c6990355460a13a0ad951528191be76fcec5c71a32e8c28b77de129a`
- `PA73_MAX_TOKENS=1024`
- `PA73_TEMPERATURE=0.0`
- `PA73_ALLOWED_REGIONS=us-west-2`
- `PA73_GUARDRAIL_APPROVED=1`

The separate Guardrail policy-test gate is `PA73_GUARDRAIL_POLICY_TEST=1`; disabled execution reads only that gate and performs no fixture, SDK, client, session, profile, or credential I/O. After enablement it requires `PA73_GUARDRAIL_POLICY_AWS_REGION=us-west-2`, `PA73_GUARDRAIL_POLICY_AWS_PROFILE=default`, `PA73_GUARDRAIL_POLICY_AWS_CREDENTIAL_MODE=profile`, the exact non-ARN Guardrail ID supplied only at execution, `PA73_GUARDRAIL_VERSION=DRAFT`, the same Core policy-version binding above, and `PA73_GUARDRAIL_POLICY_APPROVED=1`. Any numeric or other policy-test version fails before fixture read and before SDK/client I/O. The runner then validates only the fixed Guardrail policy fixture path against the recorded exact SHA-256 before any lazy production boundary can be created. This DRAFT-only path is separate from main Bedrock live evidence, which accepts only an immutable numeric version matching `^[1-9][0-9]*$` and rejects `DRAFT` before SDK/client I/O.

Access key, secret key, and session token values are not runner arguments or custom environment settings and must never be read by the runner. The approved credential mode is the named AWS profile only.

## Preflight and execution sequence

1. Read only the explicit gate; stop immediately if disabled.
2. Validate every required configuration value without SDK import, credential resolution, or client creation.
3. Read only the fixed fixture path, compare the exact approved SHA-256, enforce the strict wrapper shape, and instantiate the formal `ReasoningContextDTO`.
4. Lazily create the one-attempt control boundary.
5. Call `GetModelInvocationLoggingConfiguration` once. Continue only when `textDataDeliveryEnabled` is explicitly `false`; missing, malformed, enabled, or unknown state fails closed.
6. Primary: lifecycle preflight → formal health check → formal generate.
7. Fallback: lifecycle preflight → formal health check → formal generate.
8. Emit exactly one bounded JSON summary and stop.

Execution uses `ExplicitLiveBedrockClient`, `Boto3BedrockReasoningInvoker`, `BedrockReasoningProvider`, Core `ReasoningHealthCheckRequestDTO`, and Core `GenerateRequestDTO`. It does not bypass provider schema, citation, deadline, intervention, or result validation. A role succeeds only when lifecycle is active, health is healthy, generation returns a formal valid `ReasoningResultDTO`, and every Evidence/Analysis citation belongs to the fixed context.

## Safety and evidence retention

Before model operations, Model Invocation Logging text delivery must be explicitly false/disabled. The runner never enables logging and never creates a destination. Production Converse trace remains `disabled`.

No full prompt, response, assessment, SDK exception, fact text, inference, conclusion, excerpt, endpoint, credential, token, request ID, ARN, Account ID, UserId, CloudTrail identifier, or CloudWatch resource identifier may appear in output. The single summary is limited to schema version, fixture hash, region, role lifecycle/health/generate booleans, fixed local error, operation counts, retry count zero, non-sensitive version binding, and completion time.

A late result is discarded. No raw input or model output is printed. Failures use fixed local codes and reveal no provider or file-system detail.

## Completion rule

PA73 remains incomplete until the Guardrail fixtures pass, human review occurs, an immutable numeric version is created and bound, both primary and fallback complete lifecycle/health/generate successfully, citation and schema validations pass, and all local regression/quality checks pass. No checkbox or task status is changed by local preparation or by this plan.

This document records the prior non-sensitive DRAFT-created state and approval for bounded remaining execution. This blocker-fix change did not perform provisioning, credential access, Guardrail testing, Converse, resource mutation, or live evidence.
