terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project = var.name
    }
  }
}

module "network" {
  source = "./modules/network"

  name               = var.name
  vpc_cidr           = var.vpc_cidr
  public_subnet_cidr = var.public_subnet_cidr
  ssh_cidrs          = var.ssh_cidrs
  public_tcp_ports   = toset([80, 443, var.backend_port, var.dashboard_port])
}

module "ec2" {
  source = "./modules/ec2"

  name              = var.name
  ami_id            = var.ami_id
  subnet_id         = module.network.public_subnet_id
  security_group_id = module.network.security_group_id
  instance_type     = var.instance_type
  key_name          = var.key_name
}

module "iam" {
  source = "./modules/iam"

  name         = var.name
  instance_arn = module.ec2.instance_arn
  aws_region   = var.aws_region
}
