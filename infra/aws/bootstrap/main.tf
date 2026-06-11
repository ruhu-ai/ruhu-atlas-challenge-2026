# Bootstrap: creates the S3 bucket + DynamoDB table that the main AWS
# Terraform module uses for remote state. Run ONCE per AWS account, with
# LOCAL state (no backend block here on purpose). After this applies,
# the main module's `00-backend.tf` can find the bucket and lock table.
#
# Usage:
#   cd infra/aws/bootstrap
#   terraform init
#   terraform apply -var="region=us-east-1" -var="prefix=ruhu"

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type        = string
  description = "AWS region for the state bucket + lock table"
  default     = "us-east-1"
}

variable "prefix" {
  type        = string
  description = "Resource name prefix (keep stable; renaming triggers state migration)"
  default     = "ruhu"
}

# ── S3 bucket holding the .tfstate files (one object per workspace) ─────────
resource "aws_s3_bucket" "tf_state" {
  bucket = "${var.prefix}-tf-state"

  # Bootstrap resources are sticky on purpose — destroying them strands
  # every other Terraform module's state. Override only with intent.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── DynamoDB table for state locking ────────────────────────────────────────
resource "aws_dynamodb_table" "tf_locks" {
  name         = "${var.prefix}-tf-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "state_bucket" {
  value       = aws_s3_bucket.tf_state.bucket
  description = "Set this in infra/aws/00-backend.tf (variable `state_bucket`)"
}

output "lock_table" {
  value       = aws_dynamodb_table.tf_locks.name
  description = "Set this in infra/aws/00-backend.tf (variable `lock_table`)"
}
