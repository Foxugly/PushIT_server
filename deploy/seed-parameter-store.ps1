<#
.SYNOPSIS
  Seed AWS SSM Parameter Store (/pushit/prod/*, eu-west-1) from a local .env.

.DESCRIPTION
  Source of truth for prod env vars is SSM, NOT a .env on the server.
  Requires the AWS CLI configured with creds allowing ssm:PutParameter.
  Idempotent (--overwrite). --overwrite does NOT change a parameter's Type:
  to promote String -> SecureString, delete-parameter first, then re-seed.

  After seeding, apply on the server (see CLAUDE.md):
    sudo systemctl restart pushit-env-fetch
    sudo systemctl restart pushit-api-gunicorn pushit-api-celery pushit-api-celery-beat

.EXAMPLE
  ./deploy/seed-parameter-store.ps1 ./prod.env
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$EnvFile
)
$ErrorActionPreference = "Stop"

$SsmPrefix = "/pushit/prod"
$AwsRegion = "eu-west-1"

# Keys whose values are secrets -> SecureString (KMS key aws/ssm). Rest -> String.
$SecretKeys = @(
    "DJANGO_SECRET_KEY", "DATABASE_PASSWORD", "GRAPH_CLIENT_SECRET",
    "METRICS_AUTH_TOKEN", "EXCHANGE_CERT_PASSWORD"
)

if (-not (Test-Path -LiteralPath $EnvFile)) { throw "No such file: $EnvFile" }

foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^\s*$' -or $line -match '^\s*#') { continue }
    $idx = $line.IndexOf('=')
    if ($idx -lt 1) { continue }

    $key = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1)
    if ([string]::IsNullOrWhiteSpace($key)) { continue }

    $type = if ($SecretKeys -contains $key) { "SecureString" } else { "String" }

    Write-Host "  put $SsmPrefix/$key  ($type)"
    aws ssm put-parameter `
        --name "$SsmPrefix/$key" `
        --value "$value" `
        --type $type `
        --overwrite `
        --region $AwsRegion | Out-Null
}

Write-Host "Done. Seeded $SsmPrefix/* in $AwsRegion."
Write-Host "Re-fetch on the server:"
Write-Host "  sudo systemctl restart pushit-env-fetch"
Write-Host "  sudo systemctl restart pushit-api-gunicorn pushit-api-celery pushit-api-celery-beat"
