terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.9"
    }
  }
}

resource "local_file" "application_config" {
  filename        = "${path.module}/generated/app.conf"
  content         = "APP_ENV=production\n"
  file_permission = "0644"
}
