# CryptoTrust Agent AWS deployment foundation

The current target is an ECS Fargate web/API container behind an Application
Load Balancer, with SQS reserved for asynchronous Formal Runs and S3 for
artifacts.  API Gateway or a Lambda BFF may later submit jobs, but it must not
hold a request open for the 900-second workflow.

The template creates resources only when you explicitly run CloudFormation.
Nothing in this directory automatically writes to AWS.

## Why the run is asynchronous

API Gateway REST integrations normally have a short integration timeout, while
the CryptoTrust Formal Run has a 900-second hard deadline.  The production flow
therefore needs to be:

1. UI/API validates the Cognito JWT and request.
2. API returns `task_id` quickly and queues the Formal Run.
3. Step Functions/ECS Fargate executes the bounded workflow.
4. UI polls status and downloads the artifact bundle after publication.

AWS references:

- <https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-execution-service-limits-table.html>
- <https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html>
- <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/working-with-templates.html>
- <https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html>

## Prerequisites

- An AWS hackathon role allowed to create CloudFormation, IAM, ECR, ECS,
  Elastic Load Balancing, EC2 security groups, S3, SQS, Cognito and CloudWatch.
- Two public subnets in different Availability Zones in an existing VPC.
- Docker and AWS CLI, or AWS CloudShell with a container build environment.
- PA73/PA74 and the production composition gates in
  [DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md) completed before starting
  the service.

Do not store workshop access keys in `.env`, Git, Docker build arguments or
CloudFormation parameters.  Prefer the workshop-provided role/session.

## Stage 1: create the foundation with zero tasks

Use a syntactically valid future production module while `DesiredCount=0`.
No container starts at this stage.

```powershell
$region = "REPLACE_REGION"
$vpcId = "vpc-REPLACE"
$subnets = "subnet-REPLACE-A,subnet-REPLACE-B"

aws cloudformation deploy `
  --region $region `
  --stack-name cryptotrust-hackathon `
  --template-file deploy/aws/ecs-preview.json `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    VpcId=$vpcId `
    PublicSubnetIds=$subnets `
    AsgiAppModule=crypto_trust_agent.presentation.api.production_composition:app `
    DesiredCount=0
```

Get the newly created ECR URI:

```powershell
$repositoryUri = aws cloudformation describe-stacks `
  --region $region `
  --stack-name cryptotrust-hackathon `
  --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" `
  --output text
```

## Stage 2: build and push after PA73/PA74

Use the Git commit as an immutable image tag.  Do not use `latest`.

```powershell
$imageTag = git rev-parse --short=12 HEAD
$registry = $repositoryUri.Split('/')[0]

aws ecr get-login-password --region $region |
  docker login --username AWS --password-stdin $registry

docker build --pull --tag "${repositoryUri}:${imageTag}" .
docker push "${repositoryUri}:${imageTag}"
```

## Stage 3: start one reviewed task

The ASGI module below must exist and must be the reviewed production
composition.  Pass exact Secrets Manager and Bedrock ARNs only after PA73/PA74
review.

```powershell
aws cloudformation deploy `
  --region $region `
  --stack-name cryptotrust-hackathon `
  --template-file deploy/aws/ecs-preview.json `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    VpcId=$vpcId `
    PublicSubnetIds=$subnets `
    ImageTag=$imageTag `
    AsgiAppModule=crypto_trust_agent.presentation.api.production_composition:app `
    RuntimeSecretArn=REPLACE_SECRET_ARN `
    BedrockModelArn=REPLACE_APPROVED_MODEL_ARN `
    DesiredCount=1
```

Read `PreviewUrl` from the stack outputs.  If no ACM certificate and matching
DNS hostname are configured, the URL is HTTP-only and may be used only for a
non-credentialed preview.

## Stop compute after a rehearsal

Update the same stack with `DesiredCount=0`.  This stops Fargate tasks while
retaining the reviewed foundation, ECR images, artifacts and logs.  Delete the
stack only when the team is ready to remove the non-retained resources.

