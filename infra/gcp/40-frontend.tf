# Frontend hosting — GCS bucket fronted by an HTTPS load balancer with
# Cloud CDN. CI builds the Vite app and uploads via:
#   gsutil -m rsync -d frontend/dist gs://<bucket>/
# Then invalidates the CDN cache:
#   gcloud compute url-maps invalidate-cdn-cache <url-map> --path '/*'

# ── GCS bucket ─────────────────────────────────────────────────────────────
resource "google_storage_bucket" "frontend" {
  name                        = "${local.name}-frontend-${var.project_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html" # SPA fallback
  }
}

# Public read — CDN expects the bucket objects to be readable. Lock down
# with signed URLs later if you ever serve private assets here.
resource "google_storage_bucket_iam_member" "frontend_public" {
  bucket = google_storage_bucket.frontend.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# ── HTTPS load balancer in front of the bucket ────────────────────────────
resource "google_compute_backend_bucket" "frontend" {
  name        = "${local.name}-frontend-bb"
  bucket_name = google_storage_bucket.frontend.name
  enable_cdn  = true

  cdn_policy {
    cache_mode         = "CACHE_ALL_STATIC"
    default_ttl        = 3600
    client_ttl         = 3600
    max_ttl            = 86400
    negative_caching   = true
    serve_while_stale  = 86400
    request_coalescing = true
  }
}

resource "google_compute_url_map" "frontend" {
  name            = "${local.name}-frontend-url-map"
  default_service = google_compute_backend_bucket.frontend.id
}

resource "google_compute_target_http_proxy" "frontend" {
  name    = "${local.name}-frontend-http-proxy"
  url_map = google_compute_url_map.frontend.id
}

resource "google_compute_global_address" "frontend" {
  name = "${local.name}-frontend-ip"
}

resource "google_compute_global_forwarding_rule" "frontend_http" {
  name                  = "${local.name}-frontend-http-fr"
  target                = google_compute_target_http_proxy.frontend.id
  port_range            = "80"
  ip_address            = google_compute_global_address.frontend.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# HTTPS path is gated on a managed cert + custom domain. When
# `frontend_domain_name` is set, CI/ops can add a managed cert resource
# and a separate forwarding rule on 443. Left out here to keep the
# default deploy domain-free.
