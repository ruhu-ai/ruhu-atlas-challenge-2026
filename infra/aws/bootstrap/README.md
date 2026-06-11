# AWS Bootstrap

Creates the S3 bucket + DynamoDB table that the main AWS Terraform module
uses for remote state. Run **once per AWS account**.

```bash
cd infra/aws/bootstrap
terraform init
terraform apply -var="region=us-east-1" -var="prefix=ruhu"
```

After this applies:

- `ruhu-tf-state` (S3 bucket, versioned + encrypted) holds all state files
- `ruhu-tf-locks` (DynamoDB) coordinates concurrent applies

The main module ([`infra/aws/00-backend.tf`](../00-backend.tf)) hard-codes
those names. If you change `prefix` or `region`, update the backend block.

**This bootstrap stack itself uses LOCAL state.** That state lives in
`infra/aws/bootstrap/terraform.tfstate` and is gitignored. Don't lose it —
re-applying with a fresh state would try to recreate the bucket and fail
because of `prevent_destroy`.
