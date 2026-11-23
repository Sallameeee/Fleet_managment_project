data "aws_partition" "current" {}

resource "aws_iam_user" "deployment" {
  name = "${var.name}-github-deploy"
}

resource "aws_iam_user_policy" "deployment" {
  name = "deploy-via-ssm"
  user = aws_iam_user.deployment.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "ssm:SendCommand"
        Resource = [
          var.instance_arn,
          "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}::document/AWS-RunShellScript",
        ]
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetCommandInvocation"
        Resource = "*"
      },
    ]
  })
}
