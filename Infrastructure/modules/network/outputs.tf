output "public_subnet_id" {
  value      = aws_subnet.public.id
  depends_on = [aws_route_table_association.public]
}

output "security_group_id" {
  value      = aws_security_group.instance.id
  depends_on = [aws_vpc_security_group_egress_rule.internet]
}
