variable "name" {
  description = "Name prefix for infrastructure and IAM resources."
  type        = string
}

variable "aws_region" {
  description = "AWS region for the deployment."
  type        = string
}

variable "vpc_cidr" {
  description = "IPv4 CIDR for the VPC."
  type        = string
}

variable "public_subnet_cidr" {
  description = "IPv4 CIDR for the public subnet within the VPC."
  type        = string
}

variable "ssh_cidrs" {
  description = "IPv4 CIDRs allowed to connect over SSH. Use an empty set to disable SSH ingress."
  type        = set(string)
}

variable "key_name" {
  description = "Name of an existing EC2 key pair in this region for SSH access."
  type        = string
  default     = null
}

variable "instance_type" {
  description = "x86_64 EC2 instance type."
  type        = string
}

variable "ami_id" {
  description = "Organization-approved standard Amazon Linux 2023 x86_64 AMI ID in aws_region, with SSM Agent installed."
  type        = string
  nullable    = false
}

variable "backend_port" {
  description = "Public host port for the backend container."
  type        = number
}

variable "dashboard_port" {
  description = "Public host port for the dashboard container."
  type        = number
}
