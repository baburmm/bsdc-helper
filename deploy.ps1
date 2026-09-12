<#
.SYNOPSIS
  Deploys BSDC Helper to Azure Container Apps with an Azure Storage backend.

.DESCRIPTION
  Creates (or reuses) a resource group, a storage account + blob container, and a
  Container App built straight from this source folder. Run it once to deploy;
  run it again after code changes to push a new revision.

.EXAMPLE
  .\deploy.ps1 -ResourceGroup rg-bsdc-helper -Location eastus -AppName bsdc-helper
#>
[CmdletBinding()]
param(
  [string]$ResourceGroup = "rg-bsdc-helper",
  [string]$Location      = "eastus",
  [string]$AppName       = "bsdc-helper",
  [string]$EnvName       = "cae-bsdc-helper",
  [string]$StorageAccount,
  [string]$BlobContainer = "uploads",
  [int]$MaxUploadMb      = 512,
  [string]$AllowedExtensions = "zip,pptx,ppt,xlsx,xls,xlsm,csv"
)

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
  throw "Azure CLI not found. Install it from https://aka.ms/installazurecli and run 'az login'."
}

# Storage account names must be globally unique, 3-24 lowercase alphanumerics.
if (-not $StorageAccount) {
  $suffix = -join ((1..8) | ForEach-Object { "abcdefghijklmnopqrstuvwxyz0123456789"[(Get-Random -Max 36)] })
  $StorageAccount = "stbsdc$suffix"
}

Step "Collecting the admin password"
$secure = Read-Host "Choose the admin password for the web app" -AsSecureString
$AdminPassword = [System.Net.NetworkCredential]::new("", $secure).Password
if ($AdminPassword.Length -lt 12) { throw "Use a password of at least 12 characters." }
$SecretKey = python -c "import secrets;print(secrets.token_urlsafe(32))"
if (-not $SecretKey) { throw "Could not generate SECRET_KEY (is python on PATH?)." }

Step "Registering providers and the containerapp extension"
az extension add --name containerapp --upgrade --only-show-errors | Out-Null
az provider register --namespace Microsoft.App --wait --only-show-errors | Out-Null
az provider register --namespace Microsoft.OperationalInsights --wait --only-show-errors | Out-Null

Step "Resource group: $ResourceGroup ($Location)"
az group create --name $ResourceGroup --location $Location --only-show-errors | Out-Null

Step "Storage account: $StorageAccount"
az storage account create `
  --name $StorageAccount --resource-group $ResourceGroup --location $Location `
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 `
  --allow-blob-public-access false --only-show-errors | Out-Null

$connectionString = az storage account show-connection-string `
  --name $StorageAccount --resource-group $ResourceGroup --query connectionString -o tsv

Step "Blob container: $BlobContainer"
az storage container create --name $BlobContainer --connection-string $connectionString --only-show-errors | Out-Null

Step "Container Apps environment: $EnvName"
az containerapp env create --name $EnvName --resource-group $ResourceGroup --location $Location --only-show-errors | Out-Null

Step "Building and deploying $AppName (this takes a few minutes on first run)"
az containerapp up `
  --name $AppName `
  --resource-group $ResourceGroup `
  --environment $EnvName `
  --location $Location `
  --source . `
  --ingress external `
  --target-port 8000 `
  --only-show-errors | Out-Null

Step "Applying secrets and configuration"
az containerapp secret set --name $AppName --resource-group $ResourceGroup --secrets `
  "admin-password=$AdminPassword" "secret-key=$SecretKey" "storage-conn=$connectionString" `
  --only-show-errors | Out-Null

az containerapp update --name $AppName --resource-group $ResourceGroup `
  --min-replicas 0 --max-replicas 1 `
  --set-env-vars `
    "ADMIN_PASSWORD=secretref:admin-password" `
    "SECRET_KEY=secretref:secret-key" `
    "AZURE_STORAGE_CONNECTION_STRING=secretref:storage-conn" `
    "BLOB_CONTAINER=$BlobContainer" `
    "MAX_UPLOAD_MB=$MaxUploadMb" `
    "ALLOWED_EXTENSIONS=$AllowedExtensions" `
  --only-show-errors | Out-Null

$fqdn = az containerapp show --name $AppName --resource-group $ResourceGroup `
  --query properties.configuration.ingress.fqdn -o tsv

Write-Host "`nDone." -ForegroundColor Green
Write-Host "  URL:     https://$fqdn"
Write-Host "  Storage: $StorageAccount / $BlobContainer"
Write-Host "`nSign in with the password you just chose. Redeploy after code changes with:"
Write-Host "  az containerapp up --name $AppName --resource-group $ResourceGroup --source ."
