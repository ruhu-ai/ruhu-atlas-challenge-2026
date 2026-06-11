output "cloud_run_service_url" {
  description = "Public URL of the Cloud Run service"
  value       = google_cloud_run_v2_service.backend.uri
}

output "cloud_run_service_name" {
  value = google_cloud_run_v2_service.backend.name
}

output "artifact_registry_repo" {
  description = "Push images here: docker push <region>-docker.pkg.dev/<project>/<repo>/backend:<tag>"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.backend.repository_id}"
}

output "migrate_job_name" {
  description = "Run before flipping the service: gcloud run jobs execute <name> --region <region>"
  value       = google_cloud_run_v2_job.migrate.name
}

output "frontend_bucket" {
  description = "Upload built assets here: gsutil -m rsync -d frontend/dist gs://<bucket>/"
  value       = google_storage_bucket.frontend.name
}

output "frontend_load_balancer_ip" {
  description = "Point your DNS A record at this IP"
  value       = google_compute_global_address.frontend.address
}

output "cloudsql_instance" {
  value = google_sql_database_instance.main.name
}

output "cloudsql_private_ip" {
  description = "Private IP for Cloud SQL (reachable from Cloud Run via VPC connector)"
  value       = google_sql_database_instance.main.private_ip_address
  sensitive   = true
}

output "secret_names_to_populate" {
  description = "Run `gcloud secrets versions add <name> --data-file=-` for each (or pipe a value)"
  value       = [for s in google_secret_manager_secret.app_secrets : s.secret_id]
}

output "attachments_bucket" {
  value = google_storage_bucket.attachments.name
}

output "service_account_email" {
  description = "Cloud Run runs as this SA. Grant it any extra roles you add later."
  value       = google_service_account.run_sa.email
}
