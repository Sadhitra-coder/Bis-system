# ==============================================================================
# Azure Container Apps Deployment Script for Bis-system (V5)
# ==============================================================================
param (
    [Parameter(Mandatory = $false)]
    [string]$ResourceGroup = "Storyvord-Test",

    [Parameter(Mandatory = $false)]
    [string]$Location = "eastus",

    [Parameter(Mandatory = $false)]
    [string]$AcrName = "complywiseacr",

    [Parameter(Mandatory = $false)]
    [string]$ContainerAppName = "bis-system-v5",

    [Parameter(Mandatory = $false)]
    [string]$InternalKey = "complywise_bis_secret_v5"
)

Write-Host "=== Starting Azure Container Apps Deployment for Bis-system V5 ===" -ForegroundColor Cyan

# 1. Check Azure CLI
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI ('az') is not installed or not in PATH. Please install Azure CLI from https://aka.ms/installazurecliwindows"
    exit 1
}

# 2. Verify az login
Write-Host "Verifying Azure authentication..." -ForegroundColor Yellow
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "Not logged in. Running 'az login'..." -ForegroundColor Yellow
    az login
}

# 3. Create Resource Group if not exists
Write-Host "Ensuring Resource Group '$ResourceGroup' exists in '$Location'..." -ForegroundColor Yellow
az group create --name $ResourceGroup --location $Location --output none

# 4. Create ACR if not exists
Write-Host "Ensuring Azure Container Registry '$AcrName' exists..." -ForegroundColor Yellow
az acr create --resource-group $ResourceGroup --name $AcrName --sku Basic --admin-enabled true --output none

# 5. Build and Push Container Image to ACR (Cloud Build - no local Docker daemon needed)
Write-Host "Building container image in ACR (az acr build)..." -ForegroundColor Yellow
az acr build --registry $AcrName --image "${ContainerAppName}:v5" .

# 6. Deploy Container App via Bicep
$imageUri = "${AcrName}.azurecr.io/${ContainerAppName}:v5"
Write-Host "Deploying Container App using Bicep with image '$imageUri'..." -ForegroundColor Yellow

$deployment = az deployment group create `
    --resource-group $ResourceGroup `
    --template-file azure-container-app.bicep `
    --parameters containerImage=$imageUri internalServiceKey=$InternalKey `
    --output json | ConvertFrom-Json

$fqdn = $deployment.properties.outputs.fqdn.value
Write-Host "`n=== Azure Deployment Completed Successfully! ===" -ForegroundColor Green
Write-Host "BIS Service URL: https://$fqdn" -ForegroundColor Green
Write-Host "`nVerifying health endpoint:" -ForegroundColor Cyan
Invoke-RestMethod -Uri "https://$fqdn/health" -Method Get | ConvertTo-Json

Write-Host "`nNext Step: Add to ComplyWise Vercel Backend:" -ForegroundColor Cyan
Write-Host "vercel env add BIS_AGENT_BASE_URL production,preview,development --value 'https://$fqdn' --project backend --yes"
