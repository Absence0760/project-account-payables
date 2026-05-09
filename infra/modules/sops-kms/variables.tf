variable "env" {
  description = "Environment slug (preview, prod). Used in key alias + tags."
  type        = string
  validation {
    condition     = contains(["preview", "prod"], var.env)
    error_message = "env must be one of: preview, prod."
  }
}

variable "operator_role_arns" {
  description = "IAM principals that can encrypt / decrypt with this key (humans + CI deploy roles). The account root is added automatically so root + Identity Center admin can break-glass."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags to merge into the key + alias."
  type        = map(string)
  default     = {}
}
