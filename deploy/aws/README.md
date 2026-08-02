# CryptoTrust Agent — fastest AWS demo deployment

This path deploys one public ECS Fargate task behind an Application Load
Balancer. The task uses its IAM role to call Bedrock Claude, reads public
Binance daily OHLCV and public Google News RSS, and serves the existing Demo UI.

It is intentionally a hackathon demo: one task, in-memory run/artifact state,
no sign-in screen, and synchronous submission. Do not present it as the final
high-availability production architecture.

Nothing runs until these commands are executed. Never commit workshop access
keys; run with the workshop role or CloudShell session.

## 1. Create the stack with zero running tasks

```powershell
$region = "us-west-2"
$vpcId = "vpc-REPLACE"
$subnets = "subnet-REPLACE-A,subnet-REPLACE-B"

aws cloudformation deploy `
  --region $region `
  --stack-name cryptotrust-hackathon `
  --template-file deploy/aws/ecs-preview.json `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides VpcId=$vpcId PublicSubnetIds=$subnets DesiredCount=0

$repositoryUri = aws cloudformation describe-stacks `
  --region $region `
  --stack-name cryptotrust-hackathon `
  --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" `
  --output text
```

## 2. Build and push the image

```powershell
$imageTag = git rev-parse --short=12 HEAD
$registry = $repositoryUri.Split('/')[0]

aws ecr get-login-password --region $region |
  docker login --username AWS --password-stdin $registry

docker build --pull --tag "${repositoryUri}:${imageTag}" .
docker push "${repositoryUri}:${imageTag}"
```

## 3. Start the public demo

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
    BedrockModelId=us.anthropic.claude-sonnet-4-20250514-v1:0 `
    DesiredCount=1
```

Read the public URL:

```powershell
aws cloudformation describe-stacks `
  --region $region `
  --stack-name cryptotrust-hackathon `
  --query "Stacks[0].Outputs[?OutputKey=='PreviewUrl'].OutputValue" `
  --output text
```

The workshop account was live-verified with the Claude Sonnet 4 cross-Region
inference profile above. The AWS demo first attempts the frozen global Binance
adapter, then uses a separate, provenance-preserving Binance.US demo fallback
when the global endpoint is blocked from the AWS Region.

## Stop charges after the demo

Redeploy the same stack with `DesiredCount=0`. The ALB and retained resources
can still cost money; delete the stack after the event when retained data is no
longer needed.
