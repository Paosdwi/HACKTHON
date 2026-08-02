# MODEL_CHANGE: PA73 Fallback from Sonnet 5 to Nova 2 Lite

- **Status**: `APPROVED`
- **Proposal type**: Model binding change; not a Port change
- **Affected task**: PA73 — ReasoningProvider adapter
- **Decision owner**: Core Maintainer / architecture owner
- **Submitter role**: Provider owner

> Core Maintainer approval preserves primary `anthropic.claude-opus-4-8` / `us.anthropic.claude-opus-4-8` and approves fallback `amazon.nova-2-lite-v1:0` / `us.amazon.nova-2-lite-v1:0`. Approval authorizes only the scoped Infrastructure implementation and offline tests. It does not authorize AWS live access, change PA73 task status, or complete PA73.

## Approved decision and non-authorization notice

This approved decision is limited to replacing the PA73 fallback model binding while preserving the primary binding and all Core-owned contracts and orchestration. It is not a Port revision, AWS authorization, task-status change, production configuration approval, or evidence of PA73 completion. No identity data, account identifiers, resource names, or credential material is part of this approval record.

## Problem statement

The Sonnet 5 base model, inference profile, Use Case, and Agreement offer exist. `CreateFoundationModelAgreement` is blocked by `AccessDeniedException` for the sanitized reason `private marketplace eligibility` in the hackathon account.

This is an external account-governance limitation. It is not evidence of an adapter, credential, model ID, token, routing, or Application bug. This proposal intentionally omits sensitive identifiers and the complete AWS exception.

## Approved binding decision

Keep the primary binding unchanged and replace only the fallback binding:

| Role | Binding | Current | Proposed |
|---|---|---|---|
| Primary | Base model | `anthropic.claude-opus-4-8` | unchanged: `anthropic.claude-opus-4-8` |
| Primary | Inference profile | `us.anthropic.claude-opus-4-8` | unchanged: `us.anthropic.claude-opus-4-8` |
| Fallback | Base model | `anthropic.claude-sonnet-5` | `amazon.nova-2-lite-v1:0` |
| Fallback | Inference profile | `us.anthropic.claude-sonnet-5` | `us.amazon.nova-2-lite-v1:0` |

The Amazon fallback does not depend on the Anthropic Marketplace agreement. The proposal changes neither the primary model nor the repair sequence, Core orchestration, DTOs, schemas, or Core ownership of branch selection.

## Compatibility analysis

- Core continues to provide only trusted `model_role=primary|fallback`; untrusted or unknown role values fail before provider I/O.
- The `ReasoningProvider` Port and DTOs remain unchanged. No field is added to `GenerateRequestDTO`, `RepairRequestDTO`, or `ReasoningHealthCheckRequestDTO`.
- The sequence remains: primary generate → at most one primary repair → fallback generate → validate.
- The adapter never chooses fallback, retries an attempt, or switches a model.
- Bedrock Converse remains the normalized cross-provider request/response boundary for Anthropic and Amazon.
- Provider SDK metadata stays entirely within Infrastructure.
- Nova output must pass the existing strict JSON, duplicate-key, canonical Decimal, citation graph, context-boundary, forbidden-field, and no-chain-of-thought validation without modification.
## Provider-specific health semantics

Runtime health evaluation must fail closed on every required missing, unknown, or mismatched predicate. Runtime health is capped at at most TWO control-plane calls per role, in this order:

1. `GetFoundationModelAvailability`
2. `GetInferenceProfile`

No runtime health result may claim to have revalidated model lifecycle under this two-call budget.

### Primary Anthropic role

Primary runtime health requires all of the following from those two calls:

- exact base model ID: `anthropic.claude-opus-4-8`;
- `agreementAvailability.status` is `AVAILABLE`;
- `authorizationStatus` is `AUTHORIZED`;
- `entitlementAvailability` is `AVAILABLE`;
- `regionAvailability` is `AVAILABLE`;
- exact inference profile ID: `us.anthropic.claude-opus-4-8`;
- inference profile status is `ACTIVE`.

### Fallback Amazon Nova role

Fallback runtime health must not require an Anthropic Marketplace agreement. From those two calls it requires:

- exact base model ID: `amazon.nova-2-lite-v1:0`;
- `authorizationStatus` is `AUTHORIZED`;
- `regionAvailability` is `AVAILABLE`;
- exact inference profile ID: `us.amazon.nova-2-lite-v1:0`;
- inference profile status is `ACTIVE`.

Only the provider-specific required predicates explicitly listed in this proposal are runtime-health gates. For the Amazon Nova fallback, the exact base model ID, `authorizationStatus == AUTHORIZED`, `regionAvailability == AVAILABLE`, exact inference profile ID, and profile status `ACTIVE` remain fail-closed gates when missing, unknown, or mismatched.

Amazon Nova MUST NOT be considered unhealthy merely because `agreementAvailability` is `NOT_AVAILABLE`, missing, or not applicable. Whether `entitlementAvailability` applies to Amazon Nova requires explicit approval based on actual Nova API evidence and a Core Maintainer decision; until then, it MUST NOT be added as a runtime-health gate. Inapplicable Marketplace agreement fields in an Amazon response MUST NOT override the approved Amazon-specific predicate policy; acceptance tests must demonstrate this behavior.

Fallback runtime health does not claim to revalidate model lifecycle.

### Deployment/startup lifecycle preflight

For both roles, `model lifecycle == ACTIVE` is deployment/startup preflight evidence obtained through a separate `GetFoundationModel` call. This lifecycle evidence is not refreshed by each runtime health check and must not be represented as part of the two-call runtime result.

If Core later requires every runtime health check to verify lifecycle, runtime health becomes THREE control-plane calls per role: `GetFoundationModelAvailability`, `GetInferenceProfile`, and `GetFoundationModel`. That change requires reapproval of the 3-second health deadline, IAM action and resource scope, call budget, timeout allocation, and tests before implementation.

## Guardrail compatibility

- Nova is subject to the same PA73 Bedrock Guardrail policy requirement as the primary model.
- Core `guardrail_policy_version` remains distinct from AWS `guardrailVersion`; Infrastructure may translate only through a Core-maintained, locally controlled mapping.
- A missing Guardrail ID or Guardrail version remains a live blocker.
- This proposal permits no guardrail create, update, or delete operation.
- Converse trace remains fixed to disabled.
- A guardrail intervention maps to the fixed local error `guardrail_rejected`.
- No raw assessment, prompt, context, or response may be persisted.

## Reasoning and chain-of-thought boundary

- Nova reasoning mode is disabled by default and is not enabled by the adapter.
- Any future change would require a separate Core Maintainer decision and may use only a redacted reasoning mode.
- Reasoning content, reasoning tokens, and chain-of-thought must never be returned to Core or written to a log, event, error, or fixture.
- PA73 accepts only Facts, Inferences, Conclusions, citations, limitations, watchpoints, and confidence components.
## Risks and tradeoffs

- Nova quality may differ materially from Sonnet 5.
- JSON and schema adherence must be revalidated rather than inferred from Converse compatibility.
- Citation hallucination behavior must be revalidated against the existing citation graph and context boundaries.
- Numeric consistency and canonical Decimal handling must be revalidated.
- Token use, latency, and cost may change.
- Provider-level correlated failure remains possible because Nova may serve other project AI workloads.
- Anthropic primary plus Amazon fallback reduces same-provider correlated failure compared with an Anthropic-only pair.
- Unsupported Nova Bedrock features must not be fabricated, emulated deceptively, or represented as available.
- Shared assertions must not be weakened, and no `skip` or `xfail` may be added to conceal incompatibility.
- Replay and cache identity must bind the exact provider, base model, inference profile, and model version. Nova and Sonnet output is not interchangeable.
- Guardrail behavior may differ by model and requires evidence under the same PA73 policy before use.

## Approved implementation acceptance criteria

The authorized Infrastructure implementation must demonstrate all of the following without changing Core contract semantics:

- trusted Core routing for `primary` and `fallback`, with no adapter-owned branch choice;
- exact Nova base-model and inference-profile mapping;
- an unknown model role causes zero SDK calls;
- provider-specific Anthropic and Amazon runtime health predicates proven with at most TWO control-plane calls per role—`GetFoundationModelAvailability` and `GetInferenceProfile`—with missing, unknown, or mismatched required predicates failing closed;
- inapplicable Marketplace agreement fields in an Amazon response do not override the approved Amazon-specific predicate policy or make Amazon Nova unhealthy, including when `agreementAvailability` is `NOT_AVAILABLE`, missing, or not applicable; tests must demonstrate this behavior;
- deployment/startup preflight separately proves `model lifecycle == ACTIVE` through `GetFoundationModel`, without claiming that runtime health refreshes that evidence;
- any future requirement to revalidate lifecycle on every runtime health check is treated as THREE calls per role and receives reapproval for the 3-second health deadline, IAM action and resource scope, call budget, timeout allocation, and tests;
- Converse requests contain no `toolConfig`;
- a missing Guardrail ID or version causes zero SDK calls;
- strict Nova JSON parsing, duplicate-key rejection, schema validation, and canonical Decimal validation;
- unchanged citation graph and task-scoped context validation;
- no reasoning content, reasoning tokens, or chain-of-thought crosses the Infrastructure boundary;
- deadline and cancellation enforcement, with late results discarded and unable to re-enter the pipeline;
- total provider attempts per adapter operation equal 1 and hidden retries equal 0;
- safe typed errors and fail-closed redaction with no raw vendor payload;
- a default-off live-test gate that ordinary CI cannot trigger;
- all Core-owned shared assertions pass unchanged;
- Ruff, mypy, pytest, and `git diff --check` pass for the later separately scoped change.

## Live evidence requirements

Core Maintainer approval of this proposal would not itself grant AWS authorization. Any live activity requires separate, explicit authorization and a bounded evidence plan containing all of the following:

- Nova runtime availability and inference-profile probes, capped at at most TWO control-plane calls for the fallback role: `GetFoundationModelAvailability` and `GetInferenceProfile`;
- separate deployment/startup lifecycle preflight evidence from `GetFoundationModel`, with explicit acknowledgement that runtime health does not refresh it;
- at most one controlled Nova Converse invocation;
- an exact Maintainer-designated fixture path and content hash;
- the Guardrail ID and Guardrail version supplied through the permitted local mechanism;
- narrowly scoped IAM resources for the authorized probes and invocation;
- explicit acknowledgement of cost and CloudTrail/CloudWatch side effects;
- retry count fixed to zero;
- no repair or fallback extra live calls without separate authorization;
- confirmation that Model Invocation Logging will not store the full prompt or response.

If live evidence is expanded to verify lifecycle on every runtime health check, it becomes THREE control-plane calls per role and requires the same reapproval of the 3-second health deadline, IAM action and resource scope, call budget, timeout allocation, and tests.

No live evidence may include secret material, raw provider payloads, full prompts, full responses, or chain-of-thought in repository artifacts.

## Rollback plan

If Private Marketplace later permits Sonnet 5, restoration must occur through one trusted model-configuration mapping only.

- No Core DTO or schema change is required or permitted for rollback.
- Sonnet offline evidence and separately authorized live evidence must be rerun before restoration.
- Nova and Sonnet replay results are not interchangeable; operation and replay identity must bind exact model and provider identity.
- Existing in-flight and completed operations remain bound to the model selected for that operation and must not silently replay under another model.
- Rollback must preserve Core routing, attempt limits, deadline behavior, validation, guardrail policy, and redaction.

## Ownership and approval

### Provider owner

The Provider owner may propose this binding change and supply sanitized Infrastructure evidence only. The Provider owner does not own Core orchestration, formal model-selection authority, health-contract semantics, DTOs, schemas, shared assertions, task status, or live authorization.

### Core Maintainer / architecture owner

The Core Maintainer or architecture owner decides whether to formalize the fallback replacement and its health semantics. A decision must explicitly address the runtime cap of at most TWO control-plane calls per role—`GetFoundationModelAvailability` and `GetInferenceProfile`—and separate deployment/startup lifecycle preflight through `GetFoundationModel`, whose evidence is not refreshed by each runtime check.

If Core requires lifecycle verification during every runtime health check, the decision must instead approve THREE control-plane calls per role and reapprove the 3-second health deadline, IAM action and resource scope, call budget, timeout allocation, and tests.

The approved binding decision remains subject to these limits:

- no Master Spec, requirement, design, task-checkbox, Core contract, or shared-assertion changes;
- no AWS or other live calls;
- no production configuration approval;
- no commit, push, or pull request based on this implementation authorization.

Approval of the fallback binding does not complete PA73 and does not authorize AWS live access or any task-status change.

## Non-goals and implementation guard

This proposal does not:

- change a Port, DTO, frozen schema, shared assertion, Core branch, or ownership boundary;
- modify primary model selection;
- alter the primary-repair-fallback sequence;
- introduce adapter-selected fallback, retry, model switching, tools, or hidden provider calls;
- weaken strict output, citation, Decimal, context, forbidden-field, guardrail, or no-chain-of-thought validation;
- establish AWS resource availability, IAM permission, Guardrail configuration, cost consent, or live readiness;
- claim PA73 completion or change any task status;
- permit unsupported provider features to be invented;
- permit full prompts, contexts, responses, assessments, reasoning data, or raw exceptions in persistence or evidence;
- authorize any implementation, deployment, commit, push, or pull request.

## References

- **AWS Bedrock Model Access** — https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html — Supports model access, agreement, authorization, and deployment-readiness decisions.
- **Amazon Nova 2 Lite model card** — https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html — Supports Nova 2 Lite capability and model-specific compatibility review.
- **Bedrock Converse** — https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html — Supports the normalized cross-provider runtime request/response boundary and guardrail configuration.
- **AWS Private Marketplace** — https://docs.aws.amazon.com/marketplace/latest/buyerguide/private-marketplace-current.html — Supports classifying the Sonnet agreement failure as account governance or eligibility rather than adapter logic.
