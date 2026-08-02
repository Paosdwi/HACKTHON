# Public demo readiness

## Ready in source

- PA73 adapter merged and offline regressions pass.
- Claude Opus is the primary Bedrock model.
- ECS uses the task role; no AWS key is stored in Git or environment files.
- Public Google News RSS and Binance daily closed OHLCV feed the reasoning
  context.
- Core still validates JSON shape, decimal confidence, citation IDs, timeout
  and safe error mapping.
- The existing UI publishes Final Report, Evidence List, Execution Log and
  Manifest for download.

## Verify before opening the judge URL

1. Workshop role can create CloudFormation/IAM/ECR/ECS/ALB resources.
2. Bedrock Claude Opus 4.8 is accessible from `us-west-2` through the
   `us.anthropic.claude-opus-4-8` inference profile.
3. Container image is pushed with the current Git SHA tag.
4. ECS service reaches a healthy target and CloudWatch contains no startup
   error.
5. Run one BTC question and open all four artifacts.

## Deliberate demo limitations

- Public temporary URL; no production Cognito login flow.
- One ECS task with in-memory repositories and artifacts.
- News discovery uses Google News RSS; links and availability are external.
- Market data is daily closed OHLCV, not tick-by-tick pricing.
- S3/SQS/Cognito resources are prepared by the stack but not part of this
  fastest synchronous demo path.
- Guardrail resource provisioning and the formal PA73 live-evidence ceremony
  are deferred; prompt-injection isolation, strict output parsing and citation
  checks remain active in code.
