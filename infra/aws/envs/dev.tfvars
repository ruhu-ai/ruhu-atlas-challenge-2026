# AWS dev sizing — smallest viable footprint.

region      = "us-east-1"
name_prefix = "ruhu"

fargate_cpu           = 256
fargate_memory        = 512
service_desired_count = 1
web_concurrency       = 1
image_tag             = "latest"

rds_instance_class       = "db.t4g.micro"
rds_allocated_storage_gb = 20
rds_engine_version       = "16.4"
rds_deletion_protection  = false

vpc_cidr             = "10.20.0.0/16"
public_subnet_cidrs  = ["10.20.1.0/24", "10.20.2.0/24"]
private_subnet_cidrs = ["10.20.11.0/24", "10.20.12.0/24"]

frontend_domain_name = ""

app_env = {
  RUHU_ENVIRONMENT        = "development"
  VOICE_STT_LOCATION      = "us-central1"
  VOICE_STT_MODEL         = "chirp_3"
  VOICE_STT_LANGUAGE      = "en-US"
  RUHU_SYNC_DB_POOL_SIZE  = "10"
  RUHU_ASYNC_DB_POOL_SIZE = "10"
}
