# Compute — ECR repo, ECS Fargate cluster + service + task def, ALB,
# IAM roles, CloudWatch log group, and a one-shot Alembic migration task.

# ── ECR repo for the backend image ─────────────────────────────────────────
resource "aws_ecr_repository" "backend" {
  name                 = "${local.name}-backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

# ── CloudWatch log group ───────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name}-backend"
  retention_in_days = 30
}

# ── IAM roles ──────────────────────────────────────────────────────────────
# Execution role: pulls the image, writes logs, reads secret values.
resource "aws_iam_role" "task_execution" {
  name = "${local.name}-task-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow execution role to read every app secret + the DB-URL secrets.
resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "${local.name}-task-execution-secrets"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = concat(
        [for s in aws_secretsmanager_secret.app_secrets : s.arn],
        [
          aws_secretsmanager_secret.db_url_runtime.arn,
          aws_secretsmanager_secret.db_url_auth.arn,
        ],
      )
    }]
  })
}

# Task role: what the running app can call. S3 attachments + DB nothing
# extra (DB auth is via the password in the URL).
resource "aws_iam_role" "task" {
  name = "${local.name}-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task_s3" {
  name = "${local.name}-task-s3"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.attachments.arn,
        "${aws_s3_bucket.attachments.arn}/*",
      ]
    }]
  })
}

# ── ECS cluster ────────────────────────────────────────────────────────────
resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled" # cost-optimised; flip to "enabled" later
  }
}

# ── Task definition ────────────────────────────────────────────────────────
locals {
  # Build the env-from-secret list: every key in var.secret_env_keys
  # maps to a same-named Secrets Manager entry.
  task_secrets = concat(
    [
      { name = "RUHU_DATABASE_URL", valueFrom = aws_secretsmanager_secret.db_url_runtime.arn },
      { name = "RUHU_AUTH_DATABASE_URL", valueFrom = aws_secretsmanager_secret.db_url_auth.arn },
    ],
    [for k in var.secret_env_keys : {
      name      = k
      valueFrom = aws_secretsmanager_secret.app_secrets[k].arn
    }],
  )

  task_environment = concat(
    [for k, v in var.app_env : { name = k, value = v }],
    [
      { name = "PORT", value = "8000" },
      { name = "WEB_CONCURRENCY", value = tostring(var.web_concurrency) },
    ],
  )
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.fargate_cpu
  memory                   = var.fargate_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "backend"
    image     = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]

    environment = local.task_environment
    secrets     = local.task_secrets

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://127.0.0.1:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "backend"
      }
    }
  }])
}

# ── ALB ────────────────────────────────────────────────────────────────────
resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false
}

resource "aws_lb_target_group" "backend" {
  name        = "${local.name}-backend-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
}

# Plain HTTP listener for now; add HTTPS + ACM cert when a domain is decided.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

# ── ECS service ────────────────────────────────────────────────────────────
resource "aws_ecs_service" "backend" {
  name            = "${local.name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.service_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  # CI updates the image tag, which produces a new task def revision and
  # rolls the service. Don't let Terraform fight CI on `desired_count`.
  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http]
}

# ── One-shot migration task ────────────────────────────────────────────────
# Run with:
#   aws ecs run-task --cluster <cluster> --task-definition <family> \
#       --launch-type FARGATE --network-configuration ...
# The CI pipeline runs this before flipping the service to a new image tag.

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "migrate"
    image     = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
    essential = true
    command   = ["alembic", "upgrade", "head"]

    environment = local.task_environment
    secrets     = local.task_secrets

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }])
}
