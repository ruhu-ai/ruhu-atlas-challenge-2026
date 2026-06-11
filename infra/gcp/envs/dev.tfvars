# GCP dev sizing.
#
# NOT YET APPLIED — the running ruhu-dev project was built manually via
# the GCP Console. This tfvars describes the equivalent setup as the
# future-state target. Do NOT `terraform apply` this against the live
# ruhu-dev project without first either:
#
#   (a) `terraform import`-ing each existing resource into state, OR
#   (b) Migrating data to a fresh project (e.g. `ruhu-dev-2`) and applying
#       cleanly.
#
# See infra/gcp/README.md for the migration runbook.

project_id  = "ruhu-dev"
region      = "us-central1"
name_prefix = "ruhu"

cloud_run_cpu           = "1"
cloud_run_memory        = "1Gi"
cloud_run_min_instances = 0
cloud_run_max_instances = 3
cloud_run_concurrency   = 40
web_concurrency         = 1
image_tag               = "latest"

cloudsql_tier                = "db-f1-micro"
cloudsql_disk_size_gb        = 20
cloudsql_database_version    = "POSTGRES_16"
cloudsql_deletion_protection = false

vpc_cidr           = "10.40.0.0/20"
vpc_connector_cidr = "10.40.16.0/28"

frontend_domain_name = ""

app_env = {
  RUHU_ENVIRONMENT        = "development"
  VOICE_STT_LOCATION      = "us-central1"
  VOICE_STT_MODEL         = "chirp_3"
  VOICE_STT_LANGUAGE      = "en-US"
  RUHU_SYNC_DB_POOL_SIZE  = "10"
  RUHU_ASYNC_DB_POOL_SIZE = "10"
}
