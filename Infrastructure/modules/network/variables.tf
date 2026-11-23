variable "name" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "public_subnet_cidr" {
  type = string
}

variable "ssh_cidrs" {
  type = set(string)
}

variable "public_tcp_ports" {
  type = set(number)

  validation {
    condition = alltrue([
      for port in var.public_tcp_ports :
      port >= 1 && port <= 65535 && port == floor(port) && port != 22
    ])
    error_message = "Public ports must be integers between 1 and 65535, excluding SSH port 22."
  }
}
