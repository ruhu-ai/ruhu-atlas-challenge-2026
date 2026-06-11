# ── Top-level inputs ────────────────────────────────────────────────────────

variable "region" {
  type        = string
  description = "AWS region for all resources in this workspace"
  default     = "us-east-1"
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix; combined with terraform.workspace for uniqueness"
  default     = "ruhu"
}

# ── Compute sizing ──────────────────────────────────────────────────────────

variable "fargate_cpu" {
  type        = number
  description = "Fargate task CPU units (256, 512, 1024, 2048, 4096)"
  default     = 512
}

variable "fargate_memory" {
  type        = number
  description = "Fargate task memory in MB"
  default     = 1024
}

variable "service_desired_count" {
  type        = number
  description = "Number of Fargate tasks to keep running"
  default     = 1
}

variable "image_tag" {
  type        = string
  description = "Container image tag pulled from ECR (CI sets this per deploy)"
  default     = "latest"
}

variable "web_concurrency" {
  type        = number
  description = "Uvicorn worker count (also used as WEB_CONCURRENCY env var)"
  default     = 2
}

# ── Database sizing ─────────────────────────────────────────────────────────

variable "rds_instance_class" {
  type        = string
  description = "RDS instance class (db.t4g.micro for dev, db.t4g.small+ for staging)"
  default     = "db.t4g.micro"
}

variable "rds_allocated_storage_gb" {
  type        = number
  description = "RDS allocated storage in GB"
  default     = 20
}

variable "rds_engine_version" {
  type        = string
  description = "Postgres engine version"
  default     = "16.4"
}

variable "rds_deletion_protection" {
  type        = bool
  description = "Block deletion in the AWS console (true for staging+)"
  default     = false
}

# ── Networking ──────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type        = string
  description = "Primary CIDR for the VPC"
  default     = "10.20.0.0/16"
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "Public subnet CIDRs (one per AZ)"
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "Private subnet CIDRs (one per AZ; Fargate + RDS live here)"
  default     = ["10.20.11.0/24", "10.20.12.0/24"]
}

# ── Frontend ────────────────────────────────────────────────────────────────

variable "frontend_domain_name" {
  type        = string
  description = "Optional custom domain for the CloudFront distribution. Empty = use default *.cloudfront.net"
  default     = ""
}

# ── App env vars wired into the Fargate task ───────────────────────────────
# Non-secret runtime config. Keep this list short; secrets live in Secrets
# Manager and are referenced from the task definition via `secrets`.

variable "app_env" {
  type        = map(string)
  description = "Plain (non-secret) env vars applied to the Fargate task"
  default = {
    RUHU_ENVIRONMENT        = "production"
    VOICE_STT_LOCATION      = "us-central1"
    VOICE_STT_MODEL         = "chirp_3"
    VOICE_STT_LANGUAGE      = "en-US"
    RUHU_SYNC_DB_POOL_SIZE  = "20"
    RUHU_ASYNC_DB_POOL_SIZE = "20"
  }
}

# Names of Secrets Manager entries the task should mount as env vars.
# The IaC creates the entries empty; populate values out-of-band before
# the service starts. Add or remove keys here without touching the
# compute module.

variable "secret_env_keys" {
  type        = list(string)
  description = "Env-var names that map to a same-named Secrets Manager entry"
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
    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
  ]
}
