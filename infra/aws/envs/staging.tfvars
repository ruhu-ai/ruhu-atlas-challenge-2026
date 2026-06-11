# AWS staging sizing — close to a small production footprint.

region      = "us-east-1"
name_prefix = "ruhu"

fargate_cpu           = 1024
fargate_memory        = 2048
service_desired_count = 2
web_concurrency       = 2
image_tag             = "latest"

rds_instance_class       = "db.t4g.small"
rds_allocated_storage_gb = 50
rds_engine_version       = "16.4"
rds_deletion_protection  = true

# Distinct CIDR per env so VPC peering between envs stays viable later
vpc_cidr             = "10.30.0.0/16"
public_subnet_cidrs  = ["10.30.1.0/24", "10.30.2.0/24"]
private_subnet_cidrs = ["10.30.11.0/24", "10.30.12.0/24"]

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
