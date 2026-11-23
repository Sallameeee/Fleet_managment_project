output "aws_region" {
  value = var.aws_region
}

output "instance_id" {
  value = module.ec2.instance_id
}

output "public_ip" {
  value = module.ec2.public_ip
}

output "security_group_id" {
  value = module.network.security_group_id
}

output "deployment_iam_user_name" {
  value = module.iam.user_name
}

output "backend_url" {
  description = "Backend URL once the container publishes its configured host port."
  value       = "http://${module.ec2.public_ip}:${var.backend_port}"
}

output "dashboard_url" {
  description = "Dashboard URL once the container publishes its configured host port."
  value       = "http://${module.ec2.public_ip}:${var.dashboard_port}"
}
