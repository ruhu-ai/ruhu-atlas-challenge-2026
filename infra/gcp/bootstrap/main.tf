# Bootstrap: creates the GCS bucket the main GCP Terraform module uses
# for remote state. Run ONCE per GCP project, with LOCAL state.
#
# Usage:
#   cd infra/gcp/bootstrap
#   terraform init
#   terraform apply -var="project_id=ruhu-dev" -var="region=us-central1"

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "GCP project that owns the state bucket"
}

variable "region" {
  type        = string
  description = "GCS bucket location"
  default     = "us-central1"
}

variable "prefix" {
  type        = string
  description = "Bucket name prefix; final name is <prefix>-tf-state-<project_id>"
  default     = "ruhu"
}

resource "google_storage_bucket" "tf_state" {
  name                        = "${var.prefix}-tf-state-${var.project_id}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition { num_newer_versions = 10 }
    action { type = "Delete" }
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "state_bucket" {
  value       = google_storage_bucket.tf_state.name
  description = "Set this in infra/gcp/00-backend.tf"
}
