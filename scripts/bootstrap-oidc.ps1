# ─────────────────────────────────────────────────────────────────────────────
# bootstrap-oidc.ps1 — ONE-TIME setup of GitHub Actions ↔ Azure OIDC trust.
#
# Run this ONCE per repo by a tenant/sub admin. After this you never deploy
# from a laptop again — GitHub Actions does it via federated credential, no
# secrets stored anywhere.
#
# What it does:
#   1) Creates a User-Assigned Managed Identity (-UamiName, default
#      `tva-deploy-mi`) in the resource group.
#   2) Adds a federated credential trusting GitHub's OIDC issuer for a specific
#      `repo:<org>/<repo>:environment:<env>` subject.
#   3) Grants the UAMI the minimum roles: Contributor on the RG, AcrPush on
#      the ACR (if -AcrName is supplied), Key Vault Secrets Officer on the KV
#      (if -KeyVaultName is supplied).
#   4) Prints the GitHub Actions secrets you must set (AZURE_CLIENT_ID,
#      AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID).
#
# Re-run is safe — every step is idempotent. -AcrName / -KeyVaultName are
# optional; they will be granted later if you supply them on a re-run.
#
# Example:
#   .\bootstrap-oidc.ps1 -SubscriptionId <id> -ResourceGroup my-rg `
#       -GitHubOrg my-org -GitHubRepo teams_avatar_voice_live `
#       -AcrName myacr -KeyVaultName my-kv -AlsoMain
# ─────────────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string] $SubscriptionId,
    [Parameter(Mandatory=$true)] [string] $ResourceGroup,
    [Parameter(Mandatory=$true)] [string] $GitHubOrg,
    [Parameter(Mandatory=$true)] [string] $GitHubRepo,
    [string] $Environment = 'prod',
    [string] $UamiName    = 'tva-deploy-mi',
    [string] $Location    = 'swedencentral',
    [string] $AcrName     = '',
    [string] $KeyVaultName = '',
    [switch] $AlsoMain
)

$ErrorActionPreference = 'Stop'

function Step($m) { Write-Host "[oidc] $m" -ForegroundColor Cyan }

az account set --subscription $SubscriptionId | Out-Null
$tenantId = (az account show --query tenantId -o tsv)

# 1) Create UAMI (idempotent)
Step "Creating/Reusing UAMI $UamiName in $ResourceGroup"
$uami = az identity create -g $ResourceGroup -n $UamiName -l $Location | ConvertFrom-Json
if (-not $uami -or -not $uami.principalId) {
    throw "UAMI creation failed (resource group missing? wrong sub?). Aborting."
}
$uamiClientId   = $uami.clientId
$uamiPrincipal  = $uami.principalId
$uamiResourceId = $uami.id
Step "  clientId=$uamiClientId principalId=$uamiPrincipal"

# 2) Federated credential per environment
# NOTE: ${var} syntax is REQUIRED here. PowerShell parses `$GitHubRepo:` as a
# scope qualifier (like $env:), which silently empties the variable.
$subjects = @("repo:${GitHubOrg}/${GitHubRepo}:environment:${Environment}")
if ($AlsoMain) { $subjects += "repo:${GitHubOrg}/${GitHubRepo}:ref:refs/heads/main" }

foreach ($subj in $subjects) {
    $fcName = "gh-" + ($subj -replace '[^a-zA-Z0-9]','-')
    if ($fcName.Length -gt 120) { $fcName = $fcName.Substring(0,120) }
    Step "Federated credential: $fcName  subject=$subj"
    $body = @{
        name      = $fcName
        issuer    = 'https://token.actions.githubusercontent.com'
        subject   = $subj
        audiences = @('api://AzureADTokenExchange')
    } | ConvertTo-Json -Compress
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $body -Encoding UTF8
    az identity federated-credential create --identity-name $UamiName -g $ResourceGroup --name $fcName `
        --issuer 'https://token.actions.githubusercontent.com' `
        --subject $subj `
        --audiences 'api://AzureADTokenExchange' 2>$null | Out-Null
    Remove-Item $tmp -Force
}

# 3) Role assignments
function Assign([string]$role, [string]$scope) {
    $existing = az role assignment list --assignee-object-id $uamiPrincipal --assignee-principal-type ServicePrincipal --scope $scope --role $role --query "[0].id" -o tsv 2>$null
    if ($existing) { Step "  exists: $role @ $scope"; return }
    Step "  granting: $role @ $scope"
    az role assignment create --assignee-object-id $uamiPrincipal --assignee-principal-type ServicePrincipal --role $role --scope $scope | Out-Null
}

$rgScope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup"
Step 'Granting roles'
Assign 'Contributor' $rgScope

if ($AcrName) {
    $acrId = az acr show -n $AcrName -g $ResourceGroup --query id -o tsv 2>$null
    if ($acrId) { Assign 'AcrPush' $acrId } else { Step "  ACR $AcrName not found — skipping AcrPush" }
} else {
    Step "  AcrName not supplied — skipping AcrPush (re-run with -AcrName <name> once ACR is deployed)"
}

if ($KeyVaultName) {
    $kvId = az keyvault show -n $KeyVaultName -g $ResourceGroup --query id -o tsv 2>$null
    if ($kvId) { Assign 'Key Vault Secrets Officer' $kvId } else { Step "  KV $KeyVaultName not found yet — re-run after avatar-stack deploys" }
} else {
    Step "  KeyVaultName not supplied — skipping KV role grant (re-run with -KeyVaultName <name> once KV is deployed)"
}

# 4) Print GH Actions secrets
Write-Host ''
Write-Host '─────────────────────────────────────────────────────────' -ForegroundColor Green
Write-Host 'Set these GitHub repository or environment secrets:' -ForegroundColor Green
Write-Host "  AZURE_CLIENT_ID         = $uamiClientId"
Write-Host "  AZURE_TENANT_ID         = $tenantId"
Write-Host "  AZURE_SUBSCRIPTION_ID   = $SubscriptionId"
Write-Host '─────────────────────────────────────────────────────────' -ForegroundColor Green
