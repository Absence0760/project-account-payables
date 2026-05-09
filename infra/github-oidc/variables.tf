variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  description = "GitHub `<owner>/<repo>` slug allowed to assume the deploy roles."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
