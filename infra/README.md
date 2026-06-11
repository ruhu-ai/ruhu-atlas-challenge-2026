# Infrastructure

Terraform IaC for Ruhu deployments on **AWS** and **GCP**. Both clouds are
described in this repo for portability, but their applied state is
intentionally asymmetric:

| Environment | AWS | GCP | Notes |
|---|---|---|---|
| `dev` | written, **not applied** | running (manually built via Console) | App is currently tested against the manual GCP dev project. |
| `staging` | written, **not applied** | written, **not applied** | App is not yet stable enough to deploy. |
| `production` | not written | not written | Out of scope for now. |

## Why two clouds in one repo

Pre-seed budget reality: AWS credits cover hosting, but Google Cloud Speech
(`chirp_3`) is the best multilingual STT for the African languages we serve.
Once funded, AWS becomes optional; until then, the most cost-effective
deployment is **single-host on AWS, calling GCP STT/TTS from AWS compute**.

Writing IaC for both clouds now (not deploying both) gives us:

- **Optionality** — move dev to AWS in a `terraform apply` when AWS becomes
  cheaper than GCP for our voice volume.
- **Customer-ready GCP path** — if an enterprise customer requires a GCP-only
  data plane, we can stand it up in ~30 minutes by `terraform apply` against
  the GCP module.
- **No drift cost** — only one environment is applied per cloud, so nothing
  to maintain in parallel until we choose to.

## Layout

```
infra/
├── README.md                       (this file)
├── docker/
│   ├── Dockerfile                  shared backend container
│   └── .dockerignore
├── aws/
│   ├── bootstrap/                  one-shot: state bucket + DynamoDB lock table
│   ├── 00-versions.tf              provider pinning
│   ├── 00-backend.tf               S3 + DynamoDB remote state
│   ├── 00-variables.tf             all inputs (region, sizes, image tag, etc.)
│   ├── 10-network.tf               VPC, subnets, security groups
│   ├── 20-data.tf                  RDS Postgres + S3 buckets + Secrets Manager
│   ├── 30-compute.tf               ECR + ECS Fargate + ALB + IAM roles + migrations
│   ├── 40-frontend.tf              S3 + CloudFront for the Vite static build
│   ├── 99-outputs.tf
│   ├── envs/
│   │   ├── dev.tfvars              dev sizing
│   │   └── staging.tfvars          staging sizing
│   └── README.md                   AWS-specific runbook
└── gcp/
    ├── bootstrap/                  one-shot: GCS state bucket
    ├── 00-versions.tf
    ├── 00-backend.tf               GCS remote state
    ├── 00-variables.tf
    ├── 10-network.tf               VPC + serverless connector
    ├── 20-data.tf                  Cloud SQL Postgres + GCS buckets + Secret Manager
    ├── 30-compute.tf               Artifact Registry + Cloud Run + IAM SA + migrations
    ├── 40-frontend.tf              GCS + Cloud CDN for the static build
    ├── 99-outputs.tf
    ├── envs/
    │   ├── dev.tfvars              "future-state" — the running ruhu-dev was built manually
    │   └── staging.tfvars
    └── README.md                   GCP-specific runbook
```

## Conventions

- **Workspaces select environment.** `terraform workspace select dev` then
  `terraform apply -var-file=envs/dev.tfvars`. Same code path, different
  state file and sizing per workspace.
- **One Postgres instance per env, two databases inside it** — `ruhu_runtime`
  and `ruhu_auth`, matching the app's `RUHU_DATABASE_URL` /
  `RUHU_AUTH_DATABASE_URL` split. Cheapest layout that preserves the
  runtime/auth boundary.
- **Secret containers, not values.** Terraform creates the secret entries
  (Secrets Manager / Secret Manager) with no payload. Populate values
  out-of-band with `aws secretsmanager put-secret-value` or
  `gcloud secrets versions add`. Real secrets never enter the repo.
- **LiveKit hooks but no LiveKit infra.** Compute modules accept
  `RUHU_LIVEKIT_*` env-from-secret refs so adding LiveKit later is just
  populating the secrets, not refactoring.
- **Frontend infra only.** Terraform creates buckets + CDN; CI uploads the
  built assets. Avoids `terraform apply` becoming a release tool.

## Bootstrap (run once per cloud, when ready to actually deploy)

Bootstrap creates the remote state backend itself. The main IaC modules
expect remote state to already exist.

### AWS

```bash
cd infra/aws/bootstrap
terraform init
terraform apply -var="region=us-east-1" -var="prefix=ruhu"
```

This creates `ruhu-tf-state` (S3) and `ruhu-tf-locks` (DynamoDB).

### GCP

```bash
cd infra/gcp/bootstrap
terraform init
terraform apply -var="project_id=ruhu-dev" -var="region=us-central1"
```

This creates the `ruhu-tf-state-{project_id}` GCS bucket.

## Apply per environment (when ready)

```bash
# AWS dev (NOT yet applied — replace placeholders in dev.tfvars first)
cd infra/aws
terraform init -reconfigure
terraform workspace select dev || terraform workspace new dev
terraform plan -var-file=envs/dev.tfvars
terraform apply -var-file=envs/dev.tfvars
```

Same shape for `staging` and for GCP (`infra/gcp/`).

## What is deliberately NOT in this IaC (yet)

- LiveKit voice transport (will add when we self-host; currently using LiveKit
  Cloud)
- Custom domains / DNS / ACM-or-ManagedCert TLS (depends on registrar
  decisions)
- WAF / advanced security
- CI/CD pipelines (separate concern; this IaC is what CI applies)
- Multi-region / multi-AZ failover (single AZ is fine pre-seed)
- Monitoring/alerting beyond default CloudWatch / Cloud Logging
- LLM / Anthropic / OpenAI / Stripe — these are SaaS, no infra to provision

When any of these graduate from "later" to "now," add a sibling `*.tf` file
in the relevant cloud directory.
