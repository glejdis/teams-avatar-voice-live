<#
.SYNOPSIS
    Provision the Entra ID app, Graph permissions, and Teams Application
    Access Policy needed for automated Teams meeting scheduling.

.DESCRIPTION
    This script automates every imperative step that Bicep cannot handle:

      1. Creates an Entra ID app registration (default: "Avatar Bot")
      2. Adds Graph application permissions (OnlineMeetings.ReadWrite.All, Mail.Send)
      3. Grants admin consent
      4. Creates a client secret (1 year)
      5. Creates a Teams Application Access Policy
      6. Grants the policy to the meeting-organiser user
      7. Configures ACS ↔ Teams federation
      8. Prints the .env block you need

    Prerequisites:
      - Azure CLI (`az`) authenticated to the target M365 tenant
      - PowerShell module MicrosoftTeams (Install-Module MicrosoftTeams)
      - The ACS resource must already exist (deploy infra/main.bicep first)

.PARAMETER TenantId
    Azure AD / Entra ID tenant ID.

.PARAMETER OrganizerUpn
    UPN of the licensed M365 user who will organise meetings and send emails.

.PARAMETER AcsImmutableResourceId
    Immutable resource ID of the ACS resource (from Azure Portal → Properties).

.PARAMETER AppDisplayName
    Display name for the Entra ID app registration.

.EXAMPLE
    .\setup-teams-integration.ps1 `
        -TenantId "<TENANT_ID>" `
        -OrganizerUpn "admin@contoso.onmicrosoft.com" `
        -AcsImmutableResourceId "<ACS_IMMUTABLE_RESOURCE_ID>"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$TenantId,

    [Parameter(Mandatory)]
    [string]$OrganizerUpn,

    [Parameter(Mandatory)]
    [string]$AcsImmutableResourceId,

    [string]$AppDisplayName = "Avatar Bot"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Step([string]$msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }

# ── Pre-flight checks ───────────────────────────────────────────────────────

Write-Step "Checking prerequisites"

$azAccount = az account show --query "{tenant:tenantId,sub:id}" -o json 2>$null | ConvertFrom-Json
if (-not $azAccount) {
    Write-Error "Not logged in. Run: az login --tenant $TenantId"
}
if ($azAccount.tenant -ne $TenantId) {
    Write-Warn "Current tenant is $($azAccount.tenant), switching..."
    az account set --subscription (az account list --query "[?tenantId=='$TenantId'] | [0].id" -o tsv 2>$null) 2>$null
}
Write-Ok "Azure CLI authenticated (tenant: $TenantId)"

# ── 1. Entra ID app registration ────────────────────────────────────────────

Write-Step "Creating Entra ID app registration: $AppDisplayName"

$existingApp = az ad app list --display-name $AppDisplayName --query "[0].appId" -o tsv 2>$null
if ($existingApp) {
    Write-Warn "App already exists (appId: $existingApp) — reusing"
    $appId = $existingApp
} else {
    $appJson = az ad app create --display-name $AppDisplayName -o json 2>$null | ConvertFrom-Json
    $appId = $appJson.appId
    Write-Ok "Created app: $appId"
}

# ── 2. Service principal ────────────────────────────────────────────────────

Write-Step "Ensuring service principal exists"

$spId = az ad sp list --filter "appId eq '$appId'" --query "[0].id" -o tsv 2>$null
if (-not $spId) {
    $spJson = az ad sp create --id $appId -o json 2>$null | ConvertFrom-Json
    $spId = $spJson.id
    Write-Ok "Created SP: $spId"
} else {
    Write-Ok "SP already exists: $spId"
}

# ── 3. Graph application permissions ────────────────────────────────────────

Write-Step "Adding Graph application permissions"

# Get the Microsoft Graph service principal in this tenant
$graphSpId = az ad sp list --filter "displayName eq 'Microsoft Graph'" --query "[0].id" -o tsv 2>$null
if (-not $graphSpId) {
    Write-Error "Could not find Microsoft Graph service principal in tenant"
}

# Look up the actual role IDs from the tenant's Graph SP
$graphSp = az ad sp show --id $graphSpId -o json 2>$null | ConvertFrom-Json
$meetingRoleId = ($graphSp.appRoles | Where-Object { $_.value -eq "OnlineMeetings.ReadWrite.All" }).id
$mailRoleId    = ($graphSp.appRoles | Where-Object { $_.value -eq "Mail.Send" }).id

if (-not $meetingRoleId -or -not $mailRoleId) {
    Write-Error "Could not find required role IDs in Graph SP"
}
Write-Ok "OnlineMeetings.ReadWrite.All = $meetingRoleId"
Write-Ok "Mail.Send = $mailRoleId"

# Grant (admin consent) via app role assignments
foreach ($roleId in @($meetingRoleId, $mailRoleId)) {
    $body = @{
        principalId = $spId
        resourceId  = $graphSpId
        appRoleId   = $roleId
    } | ConvertTo-Json -Compress

    try {
        az rest --method POST `
            --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignments" `
            --body $body --headers "Content-Type=application/json" 2>$null | Out-Null
        Write-Ok "Granted role $roleId"
    } catch {
        # "Permission being assigned already exists" is fine
        if ($_.Exception.Message -match "already exists") {
            Write-Ok "Role $roleId already granted"
        } else {
            Write-Warn "Role grant returned: $($_.Exception.Message)"
        }
    }
}

# ── 4. Client secret ────────────────────────────────────────────────────────

Write-Step "Creating client secret (1 year)"

$secretJson = az ad app credential reset --id $appId --years 1 --display-name "setup-script" -o json 2>$null | ConvertFrom-Json
$clientSecret = $secretJson.password
Write-Ok "Secret created (starts with $($clientSecret.Substring(0,8))...)"

# ── 5. Teams Application Access Policy ──────────────────────────────────────

Write-Step "Configuring Teams Application Access Policy"

# Connect to Teams PS using az CLI tokens
$graphToken = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv 2>$null
$teamsToken = az account get-access-token --resource 48ac35b8-9aa8-4d74-927d-1f4a14a0b239 --query accessToken -o tsv 2>$null

if (-not $graphToken -or -not $teamsToken) {
    Write-Error "Could not obtain Graph/Teams tokens. Ensure az CLI is logged in to $TenantId"
}

Disconnect-MicrosoftTeams -ErrorAction SilentlyContinue
Connect-MicrosoftTeams -AccessTokens @($graphToken, $teamsToken) | Out-Null
Write-Ok "Connected to Teams PowerShell"

$policyName = "Lisa-Meeting-Policy"
$existing = Get-CsApplicationAccessPolicy -Identity "Tag:$policyName" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Warn "Policy '$policyName' already exists — skipping creation"
} else {
    New-CsApplicationAccessPolicy `
        -Identity $policyName `
        -AppIds $appId `
        -Description "Allow Lisa HR Bot to create meetings" | Out-Null
    Write-Ok "Created policy: $policyName"
}

Grant-CsApplicationAccessPolicy -PolicyName $policyName -Identity $OrganizerUpn
Write-Ok "Granted policy to $OrganizerUpn"

# ── 6. ACS ↔ Teams federation ───────────────────────────────────────────────

Write-Step "Configuring ACS ↔ Teams federation"

Set-CsTeamsAcsFederationConfiguration `
    -Identity Global `
    -EnableAcsUsers $true `
    -AllowedAcsResources @($AcsImmutableResourceId) | Out-Null
Write-Ok "ACS federation enabled for resource $AcsImmutableResourceId"

# ── 7. Get ACS connection string ────────────────────────────────────────────

Write-Step "Retrieving ACS connection string"

$acsConnStr = az communication list-key --name (
    az communication list --query "[?properties.immutableResourceId=='$AcsImmutableResourceId'].name | [0]" -o tsv 2>$null
) --resource-group (
    az communication list --query "[?properties.immutableResourceId=='$AcsImmutableResourceId'].resourceGroup | [0]" -o tsv 2>$null
) --query "primaryConnectionString" -o tsv 2>$null

if (-not $acsConnStr) {
    Write-Warn "Could not retrieve ACS connection string automatically. Find it in Azure Portal."
    $acsConnStr = "<paste-from-azure-portal>"
}

# ── Summary ──────────────────────────────────────────────────────────────────

Write-Host "`n" -NoNewline
Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Setup complete! Add these to ai-conversational-interview-agent/.env" -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Green

$envBlock = @"

# ── Azure Communication Services (Teams meeting bridge) ──────────────────
ACS_CONNECTION_STRING=$acsConnStr
ACS_PUBLIC_HTTPS_URL=https://<your-devtunnel-or-public-url>

# ── Microsoft Graph (Teams meeting creation + email invites) ─────────────
GRAPH_CLIENT_ID=$appId
GRAPH_CLIENT_SECRET=$clientSecret
GRAPH_TENANT_ID=$TenantId
GRAPH_ORGANIZER_UPN=$OrganizerUpn
"@

Write-Host $envBlock
Write-Host ""
Write-Host "⚠  Application Access Policy can take up to 24 hours to propagate." -ForegroundColor Yellow
Write-Host "   If meeting creation returns 404, wait and retry." -ForegroundColor Yellow
Write-Host ""
