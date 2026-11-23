variable "name" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "security_group_id" {
  type = string
}

variable "instance_type" {
  type = string
}

variable "ami_id" {
  description = "Amazon Linux 2023 x86_64 AMI ID with SSM Agent installed."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^ami-([0-9a-f]{8}|[0-9a-f]{17})$", var.ami_id))
    error_message = "Supply a valid EC2 AMI ID in the deployment region."
  }
}

variable "key_name" {
  type    = string
  default = null
}
