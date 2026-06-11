# Remote state — GCS bucket created by infra/gcp/bootstrap.
# The bucket name pattern is `ruhu-tf-state-${project_id}`. If you change
# `prefix` or `project_id` in bootstrap, mirror the bucket name here.
#
# Workspaces select state file: each gets `env/<workspace>/default.tfstate`
# under this prefix.

terraform {
  backend "gcs" {
    bucket = "ruhu-tf-state-ruhu-dev"
    prefix = "env"
  }
}
