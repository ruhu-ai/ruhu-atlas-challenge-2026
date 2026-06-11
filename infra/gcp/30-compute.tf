# Compute — Artifact Registry + Cloud Run service + IAM service account +
# one-shot Cloud Run Job for Alembic migrations.

# ── Artifact Registry repo for the backend image ──────────────────────────
resource "google_artifact_registry_repository" "backend" {
  repository_id = "${local.name}-backend"
  location      = var.region
  format        = "DOCKER"
  description   = "Ruhu backend container images"

  depends_on = [google_project_service.required]
}

# ── Service account the Cloud Run service runs as ─────────────────────────
resource "google_service_account" "run_sa" {
  account_id   = substr("${local.name}-run", 0, 30)
  display_name = "Ruhu Cloud Run service account (${local.workspace})"
}

# Grant the SA permission to read every app secret + the DB-URL secrets.
resource "google_secret_manager_secret_iam_member" "run_app_secrets" {
  for_each  = google_secret_manager_secret.app_secrets
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "run_db_url_runtime" {
  secret_id = google_secret_manager_secret.db_url_runtime.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "run_db_url_auth" {
  secret_id = google_secret_manager_secret.db_url_auth.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_sa.email}"
}

# Speech / TTS — the SA needs to call Google Cloud Speech APIs.
resource "google_project_iam_member" "run_speech_user" {
  project = var.project_id
  role    = "roles/speech.client"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
}

resource "google_project_iam_member" "run_tts_user" {
  project = var.project_id
  role    = "roles/cloudtts.client"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
}

# Attachments bucket access
resource "google_storage_bucket_iam_member" "run_attachments" {
  bucket = google_storage_bucket.attachments.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run_sa.email}"
}

# Cloud SQL client (allows the SA to connect via the Cloud SQL Proxy if needed;
# Cloud Run uses the private IP path via the VPC connector by default).
resource "google_project_iam_member" "run_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
}

# ── Cloud Run service ─────────────────────────────────────────────────────
locals {
  image_url = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.backend.repository_id}/backend:${var.image_tag}"

  app_env_pairs = merge(
    var.app_env,
    {
      PORT            = "8000"
      WEB_CONCURRENCY = tostring(var.web_concurrency)
    },
  )
}

resource "google_cloud_run_v2_service" "backend" {
  name     = "${local.name}-backend"
  location = var.region

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run_sa.email
    timeout         = "300s"

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.main.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = local.image_url

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
        cpu_idle = true
      }

      ports { container_port = 8000 }

      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 5
        period_seconds        = 10
        timeout_seconds       = 5
        failure_threshold     = 6
      }

      liveness_probe {
        http_get { path = "/health" }
        period_seconds  = 30
        timeout_seconds = 5
      }

      dynamic "env" {
        for_each = local.app_env_pairs
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "RUHU_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url_runtime.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "RUHU_AUTH_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url_auth.secret_id
            version = "latest"
          }
        }
      }

      dynamic "env" {
        for_each = google_secret_manager_secret.app_secrets
        content {
          name = trimprefix(env.value.secret_id, "${local.name}_")
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = "latest"
            }
          }
        }
      }
    }

    max_instance_request_concurrency = var.cloud_run_concurrency
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  # Ignore image churn — CI rolls new revisions with new tags.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.run_app_secrets,
    google_secret_manager_secret_iam_member.run_db_url_runtime,
    google_secret_manager_secret_iam_member.run_db_url_auth,
  ]
}

# Public ingress — anyone can hit /health, auth gates the rest at the app
# layer. Replace with IAP / Cloud Armor when domain + auth fronting decisions
# are made.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.backend.name
  location = google_cloud_run_v2_service.backend.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Background worker pool (RP-2.3) ───────────────────────────────────────
# Runs `python -m ruhu.worker`: consumes the unified jobs table. The API
# service enqueues and never executes background work. No HTTP surface —
# Cloud Run worker pools bill per instance and skip request routing.
# Scale with var.worker_instance_count; SKIP LOCKED claims make any count
# safe, and recurring ticks are slot-deduped across instances.
resource "google_cloud_run_v2_worker_pool" "worker" {
  name     = "${local.name}-worker"
  location = var.region

  scaling {
    manual_instance_count = var.worker_instance_count
  }

  template {
    service_account = google_service_account.run_sa.email

    # Worker pools use direct VPC egress (no serverless connector).
    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image   = local.image_url
      command = ["python"]
      args    = ["-m", "ruhu.worker"]

      resources {
        limits = {
          cpu    = var.worker_cpu
          memory = var.worker_memory
        }
      }

      dynamic "env" {
        for_each = local.app_env_pairs
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "RUHU_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url_runtime.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "RUHU_AUTH_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url_auth.secret_id
            version = "latest"
          }
        }
      }

      dynamic "env" {
        for_each = google_secret_manager_secret.app_secrets
        content {
          name = trimprefix(env.value.secret_id, "${local.name}_")
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.run_app_secrets,
    google_secret_manager_secret_iam_member.run_db_url_runtime,
    google_secret_manager_secret_iam_member.run_db_url_auth,
  ]
}

# ── One-shot migration job ────────────────────────────────────────────────
# Run before flipping the service to a new image:
#   gcloud run jobs execute ${local.name}-migrate --region ${region}

resource "google_cloud_run_v2_job" "migrate" {
  name     = "${local.name}-migrate"
  location = var.region

  template {
    template {
      service_account = google_service_account.run_sa.email
      max_retries     = 1
      timeout         = "600s"

      vpc_access {
        connector = google_vpc_access_connector.main.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = local.image_url
        # ruhu.db.run_migrations is the canonical provisioning path: fresh
        # schema -> create_all + stamp head; existing -> alembic upgrade.
        # Raw `alembic upgrade head` cannot build from empty (ORM-only tables
        # are altered by later migrations) — see docs/runbooks/migration-stall.md.
        command = ["python"]
        args = [
          "-c",
          "import os; from ruhu.db import run_migrations; run_migrations(os.environ['RUHU_DATABASE_URL'])",
        ]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        env {
          name = "RUHU_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_url_runtime.secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "RUHU_AUTH_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_url_auth.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
    ]
  }
}
