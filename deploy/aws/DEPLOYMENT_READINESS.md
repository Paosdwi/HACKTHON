# AWS deployment readiness

This directory is a **pre-PA73 deployment foundation**, not evidence that the
production Agent is ready.  The stack deliberately defaults to
`DesiredCount=0`, and the container refuses to start unless an approved
`CRYPTOTRUST_ASGI_APP` is supplied.

## Prepared now

- Python 3.12 non-root container with a read-only root filesystem.
- ECR repository with immutable tags and scan-on-push.
- ECS Fargate web service behind an Application Load Balancer.
- CloudWatch log group with parameterized demo retention.
- Encrypted, private, versioned S3 artifact bucket.
- Encrypted SQS Formal Run queue with a 960-second visibility timeout and DLQ.
- Cognito user pool/client resource placeholders for the verified JWT boundary.
- Least-privilege task role scoped to the created S3 bucket and SQS queue.
- Conditional Secrets Manager and Bedrock permissions; both are absent unless
  their exact ARNs are passed.
- Optional ACM listener.  HTTP-only mode is preview-only and must not carry
  production credentials or Cognito tokens.

## Must be completed after PA73 / PA74

1. Merge and verify the PA73 production `ReasoningProvider`.
2. Build an explicit production composition root.  It must not import or call
   `create_local_demo_fastapi_app`, `FakeReasoningProvider`, fake identity, or
   fake repositories.
3. Implement the production Cognito JWT verifier (issuer, audience, signature,
   expiry and `sub`) and browser sign-in flow.
4. Make submission asynchronous: return `task_id` promptly, enqueue the job,
   execute the 900-second workflow in a worker/Step Functions Fargate task, and
   let the UI poll status.
5. Merge PA74 persistence, artifact and event adapters before creating the
   DynamoDB schema.  This template intentionally does not invent that schema.
6. Provide an approved Bedrock model/inference-profile ARN and exact Region.
7. Create the runtime pseudonym secret in Secrets Manager; never place its
   value in this repository or CloudFormation parameters.
8. Configure DNS + ACM and deploy HTTPS before using real Cognito tokens.
9. Decide signed-URL TTL and log/artifact/evidence retention values.
10. Run T81 AWS E2E and the 900-second dress rehearsal before setting
    `DesiredCount` above zero for the judging environment.

## Runtime modes

- `local fake`: only for deterministic developer E2E, never deployed as the
  production Agent.
- `AWS preview`: infrastructure can be created with `DesiredCount=0`; this is
  useful for permission and CloudFormation review without launching tasks.
- `AWS formal`: allowed only after the above gates pass and an approved
  production ASGI module is provided.

