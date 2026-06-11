# Remote state — S3 bucket + DynamoDB lock table created by
# infra/aws/bootstrap. Each Terraform workspace (dev, staging, …)
# gets its own state file under env:/{workspace}/ruhu/terraform.tfstate.
#
# If you renamed the bucket / table in bootstrap, mirror those names here.

terraform {
  backend "s3" {
    bucket               = "ruhu-tf-state"
    key                  = "ruhu/terraform.tfstate"
    region               = "us-east-1"
    dynamodb_table       = "ruhu-tf-locks"
    encrypt              = true
    workspace_key_prefix = "env"
  }
}
