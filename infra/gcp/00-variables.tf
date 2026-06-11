# ── Top-level inputs ────────────────────────────────────────────────────────

variable "project_id" {
  type        = string
  description = "GCP project ID for this workspace's resources"
}

variable "region" {
  type        = string
  description = "Primary GCP region"
  default     = "us-central1"
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix; combined with terraform.workspace"
  default     = "ruhu"
}

# ── Compute sizing (Cloud Run) ─────────────────────────────────────────────

variable "cloud_run_cpu" {
  type        = string
  description = "vCPU per Cloud Run instance (1, 2, 4)"
  default     = "1"
}

variable "cloud_run_memory" {
  type        = string
  description = "Memory per Cloud Run instance (e.g. 512Mi, 1Gi, 2Gi)"
  default     = "1Gi"
}

variable "cloud_run_min_instances" {
  type        = number
  description = "Floor — set 0 for scale-to-zero, 1+ to avoid cold starts"
  default     = 0
}

variable "cloud_run_max_instances" {
  type        = number
  description = "Ceiling on autoscaling"
  default     = 5
}

variable "worker_instance_count" {
  type        = number
  description = "Background worker instances (python -m ruhu.worker). Claims are FOR UPDATE SKIP LOCKED, so any count is safe; 0 disables background work entirely."
  default     = 1
}

variable "worker_cpu" {
  type        = string
  description = "vCPU per worker instance"
  default     = "1"
}

variable "worker_memory" {
  type        = string
  description = "Memory per worker instance"
  default     = "1Gi"
}

variable "cloud_run_concurrency" {
  type        = number
  description = "Max concurrent requests per instance"
  default     = 40
}

variable "image_tag" {
  type        = string
  description = "Container image tag pulled from Artifact Registry"
  default     = "latest"
}

variable "web_concurrency" {
  type        = number
  description = "Uvicorn worker count (also passed as WEB_CONCURRENCY env)"
  default     = 2
}

# ── Database sizing (Cloud SQL) ────────────────────────────────────────────

variable "cloudsql_tier" {
  type        = string
  description = "Cloud SQL machine type (db-f1-micro for dev, db-custom-* for staging+)"
  default     = "db-f1-micro"
}

variable "cloudsql_disk_size_gb" {
  type        = number
  description = "Cloud SQL disk size in GB"
  default     = 20
}

variable "cloudsql_database_version" {
  type        = string
  description = "Postgres version"
  default     = "POSTGRES_16"
}

variable "cloudsql_deletion_protection" {
  type        = bool
  description = "Block deletion in the GCP console (true for staging+)"
  default     = false
}

# ── Networking ─────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type        = string
  description = "Primary subnet CIDR for Cloud Run + Cloud SQL connectivity"
  default     = "10.40.0.0/20"
}

variable "vpc_connector_cidr" {
  type        = string
  description = "/28 used by the serverless VPC connector"
  default     = "10.40.16.0/28"
}

# ── App env vars wired into Cloud Run ──────────────────────────────────────

variable "app_env" {
  type        = map(string)
  description = "Plain (non-secret) env vars for the Cloud Run service"
  default = {
    RUHU_ENVIRONMENT        = "production"
    VOICE_STT_LOCATION      = "us-central1"
    VOICE_STT_MODEL         = "chirp_3"
    VOICE_STT_LANGUAGE      = "en-US"
    RUHU_SYNC_DB_POOL_SIZE  = "20"
    RUHU_ASYNC_DB_POOL_SIZE = "20"
  }
}

variable "secret_env_keys" {
  type        = list(string)
  description = "Env-var names that map to a same-named Secret Manager entry"
  default = [
    "RUHU_AUTH_JWT_SECRET",
    "RUHU_AUTH_JWT_ISSUER",
    "RUHU_CREDENTIAL_CIPHER_PRIMARY",
    "RUHU_GEMINI_API_KEY",
    "RUHU_ATLAS_GENERATOR_API_KEY",
    "RUHU_TELNYX_API_KEY",
    "RUHU_AFRICASTALKING_API_KEY",
    "RUHU_AFRICASTALKING_USERNAME",
    "RUHU_LIVEKIT_URL",
    "RUHU_LIVEKIT_API_KEY",
    "RUHU_LIVEKIT_API_SECRET",
    "RUHU_INTERNAL_API_SECRET",
    "RUHU_PROVIDER_WEBHOOK_SECRET",
  ]
}

# ── Frontend ───────────────────────────────────────────────────────────────

variable "frontend_domain_name" {
  type        = string
  description = "Optional custom domain for the load balancer fronting GCS+CDN"
  default     = ""
}
