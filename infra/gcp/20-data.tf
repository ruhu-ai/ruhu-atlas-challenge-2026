# Data plane — Cloud SQL Postgres (one instance, two databases) + GCS
# buckets (attachments) + Secret Manager containers.
#
# IMPORTANT: secret containers are created EMPTY (no version added).
# Populate values out-of-band:
#   gcloud secrets versions add ${SECRET_NAME} --data-file=-

# ── Master password (auto-generated) ───────────────────────────────────────
resource "random_password" "cloudsql_master" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>?"
}

# ── Cloud SQL instance ────────────────────────────────────────────────────
resource "google_sql_database_instance" "main" {
  name                = "${local.name}-pg"
  database_version    = var.cloudsql_database_version
  region              = var.region
  deletion_protection = var.cloudsql_deletion_protection

  depends_on = [
    google_service_networking_connection.private,
    google_project_service.required,
  ]

  settings {
    tier              = var.cloudsql_tier
    disk_size         = var.cloudsql_disk_size_gb
    disk_type         = "PD_SSD"
    disk_autoresize   = true
    availability_type = "ZONAL" # single zone for pre-seed cost; switch to REGIONAL for HA

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
    }

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.main.id
      enable_private_path_for_google_cloud_services = true
    }

    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = false
      record_client_address   = false
    }
  }
}

resource "google_sql_user" "admin" {
  name     = "ruhu_admin"
  instance = google_sql_database_instance.main.name
  password = random_password.cloudsql_master.result
}

resource "google_sql_database" "runtime" {
  name     = "ruhu_runtime"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_database" "auth" {
  name     = "ruhu_auth"
  instance = google_sql_database_instance.main.name
}

# ── Computed DB connection strings, stored as secrets ─────────────────────
resource "google_secret_manager_secret" "db_url_runtime" {
  secret_id = "${local.name}_RUHU_DATABASE_URL"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "db_url_runtime" {
  secret = google_secret_manager_secret.db_url_runtime.id
  secret_data = format(
    "postgresql+psycopg://%s:%s@%s:5432/%s",
    google_sql_user.admin.name,
    random_password.cloudsql_master.result,
    google_sql_database_instance.main.private_ip_address,
    "ruhu_runtime",
  )
}

resource "google_secret_manager_secret" "db_url_auth" {
  secret_id = "${local.name}_RUHU_AUTH_DATABASE_URL"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "db_url_auth" {
  secret = google_secret_manager_secret.db_url_auth.id
  secret_data = format(
    "postgresql+psycopg://%s:%s@%s:5432/%s",
    google_sql_user.admin.name,
    random_password.cloudsql_master.result,
    google_sql_database_instance.main.private_ip_address,
    "ruhu_auth",
  )
}

# ── App-secret containers (empty; populate out-of-band) ───────────────────
resource "google_secret_manager_secret" "app_secrets" {
  for_each  = toset(var.secret_env_keys)
  secret_id = "${local.name}_${each.key}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

# ── GCS bucket for attachments ────────────────────────────────────────────
resource "google_storage_bucket" "attachments" {
  name                        = "${local.name}-attachments-${var.project_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning { enabled = true }

  lifecycle_rule {
    condition { age = 90 }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
  lifecycle_rule {
    condition { age = 365 }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }
  lifecycle_rule {
    condition { num_newer_versions = 5 }
    action { type = "Delete" }
  }
}
