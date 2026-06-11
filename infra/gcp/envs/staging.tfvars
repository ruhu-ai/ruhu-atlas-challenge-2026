# GCP staging sizing — close to a small production footprint.
# NOT YET APPLIED.

project_id  = "ruhu-staging"
region      = "us-central1"
name_prefix = "ruhu"

cloud_run_cpu           = "2"
cloud_run_memory        = "2Gi"
cloud_run_min_instances = 1
cloud_run_max_instances = 10
cloud_run_concurrency   = 60
web_concurrency         = 2
image_tag               = "latest"

cloudsql_tier                = "db-custom-2-7680"
cloudsql_disk_size_gb        = 50
cloudsql_database_version    = "POSTGRES_16"
cloudsql_deletion_protection = true

# Distinct CIDR per env to keep VPC peering options open later
vpc_cidr           = "10.50.0.0/20"
vpc_connector_cidr = "10.50.16.0/28"

frontend_domain_name = ""

app_env = {
  RUHU_ENVIRONMENT        = "staging"
  VOICE_STT_LOCATION      = "us-central1"
  VOICE_STT_MODEL         = "chirp_3"
  VOICE_STT_LANGUAGE      = "en-US"
  RUHU_SYNC_DB_POOL_SIZE  = "20"
  RUHU_ASYNC_DB_POOL_SIZE = "20"
  # Browser workers are disabled by default in staging. To enable them, set
  # RUHU_BROWSER_TASK_WORKER_ENABLED=true, choose allowed packs, and keep cloud
  # isolation. The app preflight rejects production local isolation.
  RUHU_BROWSER_TASK_WORKER_ENABLED        = "false"
  RUHU_BROWSER_TASK_WORKER_ADAPTER        = "disabled"
  RUHU_BROWSER_TASK_WORKER_ISOLATION_MODE = "cloud"
  RUHU_BROWSER_TASK_ALLOWED_PACKS         = ""
}
