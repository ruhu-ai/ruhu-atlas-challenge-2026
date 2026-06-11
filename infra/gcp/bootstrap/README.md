# GCP Bootstrap

Creates the GCS bucket the main GCP Terraform module uses for remote state.
Run **once per GCP project**.

```bash
cd infra/gcp/bootstrap
terraform init
terraform apply -var="project_id=ruhu-dev" -var="region=us-central1"
```

The bucket is named `ruhu-tf-state-${project_id}` (e.g. `ruhu-tf-state-ruhu-dev`).
The main module ([`infra/gcp/00-backend.tf`](../00-backend.tf)) references
this name pattern.

**Bootstrap stack uses LOCAL state** (gitignored). Don't lose
`infra/gcp/bootstrap/terraform.tfstate` — `prevent_destroy` will fight
re-creates.

## Prerequisites

The Google provider needs:

- `gcloud auth application-default login` (or a service account JSON via
  `GOOGLE_APPLICATION_CREDENTIALS`)
- The project must already exist (Terraform doesn't create projects here)
- The user/SA needs `roles/storage.admin` on the project
