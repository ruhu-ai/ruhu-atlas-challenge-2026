output "alb_dns_name" {
  description = "Public DNS for the ALB; CNAME your domain here"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "Push images here: docker push <url>:<tag>"
  value       = aws_ecr_repository.backend.repository_url
}

output "ecs_cluster_name" {
  description = "Cluster name for CI to target"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.backend.name
}

output "migrate_task_definition_family" {
  description = "Run before flipping the service: aws ecs run-task --task-definition <this>"
  value       = aws_ecs_task_definition.migrate.family
}

output "frontend_bucket" {
  description = "Upload built assets here: aws s3 sync frontend/dist s3://<bucket>/ --delete"
  value       = aws_s3_bucket.frontend.bucket
}

output "frontend_cloudfront_domain" {
  description = "CloudFront default domain (or your custom alias if configured)"
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "frontend_cloudfront_distribution_id" {
  description = "Pass to: aws cloudfront create-invalidation --distribution-id <this> --paths '/*'"
  value       = aws_cloudfront_distribution.frontend.id
}

output "rds_endpoint" {
  description = "RDS hostname (private; reachable from Fargate tasks only)"
  value       = aws_db_instance.main.address
  sensitive   = true
}

output "secret_names_to_populate" {
  description = "Run `aws secretsmanager put-secret-value --secret-id <name> --secret-string ...` for each"
  value = concat(
    [for k in var.secret_env_keys : aws_secretsmanager_secret.app_secrets[k].name],
  )
}

output "attachments_bucket" {
  value = aws_s3_bucket.attachments.bucket
}
