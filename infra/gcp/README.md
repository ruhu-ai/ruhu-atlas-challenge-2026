# GCP infra

Terraform for the GCP deployment target.

## Current state

| Environment | Status |
|---|---|
| `dev` (`ruhu-dev` project) | **Running, but built manually** via the Console. The Terraform here describes the equivalent setup but is **NOT yet the source of truth** for the live env. |
| `staging` (`ruhu-staging` project, doesn't exist yet) | Written, **not applied** — app isn't ready. |

## Topology

- VPC + subnet, serverless VPC connector for Cloud Run → private services
- Cloud SQL Postgres (private IP only, single zone), one instance with two
  databases: `ruhu_runtime` and `ruhu_auth`
- Cloud Run service (autoscaling, scale-to-zero in dev), service account
  with secret accessor + Speech/TTS client roles
- Cloud Run Job for Alembic migrations (run before each deploy)
- Artifact Registry for backend images
- GCS + Cloud CDN for the Vite static frontend
- Secret Manager containers for every app secret (populated out-of-band)

## First-time setup (when you're ready to deploy)

1. **Create the GCP project** in the Console (Terraform doesn't create
   projects). Enable billing.

2. **Authenticate the Terraform provider**:

   ```bash
   gcloud auth application-default login
   gcloud config set project ruhu-staging
   ```

3. **Bootstrap the state backend** (once per project):

   ```bash
   cd infra/gcp/bootstrap
   terraform init
   terraform apply -var="project_id=ruhu-staging" -var="region=us-central1"
   ```

4. **Initialise the main module** (note: edit `00-backend.tf` to point at
   the right state bucket if it differs from `ruhu-tf-state-ruhu-dev`):

   ```bash
   cd infra/gcp
   terraform init
   terraform workspace new staging
   terraform plan  -var-file=envs/staging.tfvars
   terraform apply -var-file=envs/staging.tfvars
   ```

5. **Populate secrets** (Terraform creates empty containers):

   ```bash
   echo -n "<value>" | gcloud secrets versions add ruhu-staging_RUHU_AUTH_JWT_SECRET --data-file=-
   # Repeat for every secret in `terraform output secret_names_to_populate`.
   ```

6. **Build + push the backend image**:

   ```bash
   REPO=$(terraform output -raw artifact_registry_repo)
   gcloud auth configure-docker us-central1-docker.pkg.dev
   docker build -f infra/docker/Dockerfile -t "$REPO/backend:latest" .
   docker push "$REPO/backend:latest"
   ```

7. **Run migrations**:

   ```bash
   JOB=$(terraform output -raw migrate_job_name)
   gcloud run jobs execute "$JOB" --region us-central1 --wait
   ```

8. **Build + upload the frontend**:

   ```bash
   cd frontend && npm run build && cd -
   BUCKET=$(terraform -chdir=infra/gcp output -raw frontend_bucket)
   gsutil -m rsync -d frontend/dist "gs://$BUCKET/"
   gcloud compute url-maps invalidate-cdn-cache ruhu-staging-frontend-url-map --path '/*'
   ```

## Migration runbook for the existing manual `ruhu-dev`

The live `ruhu-dev` project was built manually. Two options when you're
ready to bring it under Terraform:

### Option A: greenfield replace

Cleanest. Apply this Terraform into a fresh project (`ruhu-dev-2`),
migrate data, decommission old.

1. Apply Terraform to new project: `terraform apply -var-file=envs/dev.tfvars -var="project_id=ruhu-dev-2"`
2. Dump+restore Postgres data from manual CloudSQL → new CloudSQL
3. Copy GCS attachments: `gsutil -m rsync -r gs://old/ gs://new/`
4. Replicate secret values in the new Secret Manager
5. Cut the app over (env vars / DNS)
6. Decommission the manual project

### Option B: terraform import

Preserves the running project. Brittle if any setting drifts. Prefer (A).

```bash
# For each existing resource, find its GCP self-link/ID and import it:
terraform import google_sql_database_instance.main projects/ruhu-dev/instances/<id>
terraform import google_storage_bucket.attachments <bucket-name>
# ...continue for every resource. Use `terraform plan` after each to check
# for diffs; reconcile attribute mismatches by editing tfvars.
```

## CI deploy flow (when wired)

```
build image → push to Artifact Registry → execute migrate Job →
deploy new Cloud Run revision with new image → wait for healthy →
roll the worker pool to the new image →
gsutil rsync frontend/dist → invalidate CDN
```

## Process topology (RP-2.3)

Two compute units share one image and one database:

- **`<name>-backend`** (Cloud Run service): the API. Stateless; scale via
  `cloud_run_min/max_instances`. Spawns no background threads.
- **`<name>-worker`** (Cloud Run worker pool): `python -m ruhu.worker`,
  consuming the unified `jobs` table. Scale via `worker_instance_count` —
  claims are `FOR UPDATE SKIP LOCKED` and recurring ticks are slot-deduped,
  so any instance count is safe. Drains gracefully on SIGTERM.

Note: the provider was bumped `~> 5.0` → `~> 6.0` for
`google_cloud_run_v2_worker_pool` (validated against 6.50). Review
`terraform plan` for incidental 6.x diffs on existing resources before the
first apply.

## Switching environments

```bash
terraform workspace select staging
terraform apply -var-file=envs/staging.tfvars
```

## What's missing on purpose

- Custom domain + Google-managed SSL cert (one-time decision; add when ready)
- Cloud Armor / WAF
- Multi-region Cloud SQL (REGIONAL availability_type) — single zone for cost
- IAP / authenticated public ingress (currently `allUsers` invoker; auth at app layer)
- LiveKit infra (using LiveKit Cloud)
- Bedrock / Anthropic / OpenAI provisioning — these are SaaS, no infra needed
