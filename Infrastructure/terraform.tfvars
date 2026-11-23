name               = "fleet-management"
aws_region         = "eu-central-1"
vpc_cidr           = "10.42.0.0/16"
public_subnet_cidr = "10.42.1.0/24"
instance_type      = "t3.small"
backend_port       = 8080
dashboard_port     = 3000

# Set this to your organization's approved standard Amazon Linux 2023 x86_64 AMI
# with SSM Agent installed in eu-central-1 before running terraform plan.
# ami_id = "ami-..."

# Replace the example address with your public IPv4 address and set your existing EC2 key pair.
ssh_cidrs = ["192.0.2.10/32"]
key_name  = null
