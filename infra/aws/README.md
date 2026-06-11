# AWS infra

Terraform for the AWS deployment target. **Currently NOT applied** — written
for future use; the running app today is on a manually-built GCP project
(see [`../gcp/`](../gcp/)).

## Topology

- VPC with 2 public + 2 private subnets across 2 AZs
- ALB in public subnets, Fargate tasks in private subnets, RDS in private subnets
- Single NAT gateway (cost-optimised; upgrade to one-per-AZ for HA later)
- One Postgres instance, two databases (`ruhu_runtime`, `ruhu_auth`)
- ECR for the backend image, S3 + CloudFront for the frontend
- Secrets Manager containers for every app secret (populated out-of-band)

## First-time setup (when you're ready to deploy)

1. **Bootstrap the state backend** (once per AWS account):

   ```bash
   cd infra/aws/bootstrap
   terraform init
   terraform apply -var="region=us-east-1" -var="prefix=ruhu"
   ```

2. **Initialise the main module**:

   ```bash
   cd infra/aws
   terraform init
   terraform workspace new dev      # or `select dev` if it already exists
   terraform plan  -var-file=envs/dev.tfvars
   terraform apply -var-file=envs/dev.tfvars
   ```

3. **Populate secrets** (Terraform creates empty containers; values live
   out-of-repo):

   ```bash
   # See `terraform output secret_names_to_populate` for the full list.
   aws secretsmanager put-secret-value \
       --secret-id ruhu-dev/RUHU_AUTH_JWT_SECRET \
       --secret-string "<value>"

   # Repeat for every secret. The Fargate service won't start cleanly
   # until at least JWT, credential cipher, and any provider keys you
   # actually use are populated.
   ```

4. **Build + push the backend image**:

   ```bash
   ECR=$(terraform output -raw ecr_repository_url)
   aws ecr get-login-password --region us-east-1 | \
       docker login --username AWS --password-stdin "${ECR%/*}"
   docker build -f infra/docker/Dockerfile -t "$ECR:latest" .
   docker push "$ECR:latest"
   ```

5. **Run migrations** (one-shot Fargate task):

   ```bash
   CLUSTER=$(terraform output -raw ecs_cluster_name)
   FAMILY=$(terraform output -raw migrate_task_definition_family)
   aws ecs run-task --cluster "$CLUSTER" --task-definition "$FAMILY" \
       --launch-type FARGATE --network-configuration ...
   ```

6. **Build + upload the frontend**:

   ```bash
   cd frontend && npm run build && cd -
   BUCKET=$(terraform -chdir=infra/aws output -raw frontend_bucket)
   DIST=$(terraform -chdir=infra/aws output -raw frontend_cloudfront_distribution_id)
   aws s3 sync frontend/dist "s3://$BUCKET/" --delete
   aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/*'
   ```

## CI deploy flow (when wired)

```
build image → push to ECR → register new task def revision →
run migrate task → update ECS service to new task def → wait for healthy →
sync frontend → invalidate CDN
```

## Switching environments

Workspaces hold per-env state; tfvars files hold per-env sizing.

```bash
terraform workspace select staging
terraform apply -var-file=envs/staging.tfvars
```

## What's missing on purpose

- HTTPS listener (waits for a custom domain decision + ACM cert)
- WAF
- Multi-AZ NAT (cost vs HA tradeoff)
- Bedrock or AWS-native LLM/STT/TTS (we use Anthropic + GCP Speech today)
- LiveKit (using LiveKit Cloud; self-host later)
