# Networking — VPC + subnet + serverless VPC connector. The connector
# lets Cloud Run reach Cloud SQL over a private IP without going to the
# public internet.

locals {
  workspace = terraform.workspace
  name      = "${var.name_prefix}-${local.workspace}"
}

# Required APIs. Provisioning anything else fails until these are enabled.
resource "google_project_service" "required" {
  for_each = toset([
    "compute.googleapis.com",
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "vpcaccess.googleapis.com",
    "servicenetworking.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    # Voice path keeps calling these directly:
    "speech.googleapis.com",
    "texttospeech.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ── VPC ────────────────────────────────────────────────────────────────────
resource "google_compute_network" "main" {
  name                    = "${local.name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "main" {
  name          = "${local.name}-subnet"
  ip_cidr_range = var.vpc_cidr
  region        = var.region
  network       = google_compute_network.main.id

  private_ip_google_access = true
}

# ── Private services connection (Cloud SQL private IP) ────────────────────
resource "google_compute_global_address" "private_ip" {
  name          = "${local.name}-cloudsql-private-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

# ── Serverless VPC connector for Cloud Run → VPC ──────────────────────────
resource "google_vpc_access_connector" "main" {
  name          = substr("${local.name}-conn", 0, 25)
  region        = var.region
  network       = google_compute_network.main.name
  ip_cidr_range = var.vpc_connector_cidr

  min_throughput = 200
  max_throughput = 300

  depends_on = [google_project_service.required]
}
