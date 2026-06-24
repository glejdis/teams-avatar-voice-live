#Requires -Version 7.0
<#
.SYNOPSIS
    Provision Microsoft Purview sensitivity labels + a DLP policy that mirror
    governance/security/dlp-policy.yaml (governance Phase 5).

.DESCRIPTION
    Codifies the Purview side of the runtime data-protection layer so the central
    platform policy matches what the avatar enforces inline (agentgov.security).
    Reads the YAML policy and:
      - creates the sensitivity labels (from `sensitivity_labels`) + a label policy,
      - creates a DLP policy + rule (Teams / Exchange / SharePoint / OneDrive)
        using built-in sensitive info types, with block/notify by sensitivity.
    Idempotent: existing labels/policies are skipped.

    Requirements:
      - ExchangeOnlineManagement module (Connect-IPPSSession)
      - powershell-yaml module (ConvertFrom-Yaml)
      - Compliance Administrator / Information Protection admin role.

    NOTE: custom regex info types (e.g. the German social-insurance number) map to
    a Purview custom Sensitive Information Type rule package (XML) — out of scope
    here; this maps to built-in SITs and leaves a TODO for custom ones.

.EXAMPLE
    ./01-purview-labels-dlp.ps1 -PolicyPath ../../governance/security/dlp-policy.yaml
#>
[CmdletBinding()]
param(
    [string] $PolicyPath = "$PSScriptRoot/../../governance/security/dlp-policy.yaml",
    [string] $PolicyNamePrefix = 'TeamsAvatar-Lisa',
    [switch] $WhatIfOnly
)

$ErrorActionPreference = 'Stop'

foreach ($m in @('ExchangeOnlineManagement', 'powershell-yaml')) {
    if (-not (Get-Module -ListAvailable -Name $m)) {
        throw "Required module '$m' is not installed. Install-Module $m -Scope CurrentUser"
    }
}
Import-Module powershell-yaml -ErrorAction Stop

Write-Host "==> Loading policy: $PolicyPath"
$policy = Get-Content -Raw -Path $PolicyPath | ConvertFrom-Yaml

if (-not $WhatIfOnly) {
    Write-Host "==> Connecting to Security & Compliance PowerShell..."
    Connect-IPPSSession -ErrorAction Stop | Out-Null
}

# 1) Sensitivity labels --------------------------------------------------------
foreach ($entry in $policy.sensitivity_labels.GetEnumerator()) {
    $labelName = $entry.Value
    Write-Host "==> Sensitivity label: $labelName"
    if ($WhatIfOnly) { continue }
    $existing = Get-Label -Identity $labelName -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-Label -Name $labelName -DisplayName $labelName `
            -Tooltip "Avatar interview data classified as $labelName" | Out-Null
    } else {
        Write-Host "    exists, skipping."
    }
}

$labelPolicyName = "$PolicyNamePrefix-Labels"
if (-not $WhatIfOnly) {
    $allLabels = @($policy.sensitivity_labels.Values)
    if (-not (Get-LabelPolicy -Identity $labelPolicyName -ErrorAction SilentlyContinue)) {
        Write-Host "==> Publishing label policy: $labelPolicyName"
        New-LabelPolicy -Name $labelPolicyName -Labels $allLabels | Out-Null
    }
}

# 2) DLP policy + rule ---------------------------------------------------------
# Map our info types to built-in Purview SITs (custom regex -> TODO rule package).
$builtinSit = @{
    email               = $null    # email is not a Purview SIT; covered by content rules
    iban                = 'International Banking Account Number (IBAN)'
    de_tax_id           = 'Germany Tax Identification Number'
    de_social_insurance = $null    # custom SIT required (rule package)
    phone               = $null
    salary              = $null    # keyword/trainable classifier recommended
}

$sits = @()
foreach ($t in $policy.info_types) {
    $name = $builtinSit[$t.id]
    if ($name) { $sits += @{ Name = $name; minCount = 1 } }
    elseif (-not $WhatIfOnly) {
        Write-Warning "    info_type '$($t.id)' has no built-in SIT mapping; add a custom SIT rule package."
    }
}

$dlpName = "$PolicyNamePrefix-DLP"
Write-Host "==> DLP policy: $dlpName (SITs: $($sits.Name -join ', '))"
if (-not $WhatIfOnly) {
    if (-not (Get-DlpCompliancePolicy -Identity $dlpName -ErrorAction SilentlyContinue)) {
        New-DlpCompliancePolicy -Name $dlpName `
            -ExchangeLocation All -SharePointLocation All -OneDriveLocation All `
            -TeamsLocation All -Mode Enable | Out-Null

        New-DlpComplianceRule -Name "$dlpName-Rule" -Policy $dlpName `
            -ContentContainsSensitiveInformation $sits `
            -BlockAccess $true `
            -NotifyUser 'SiteAdmin', 'LastModifier' | Out-Null
    } else {
        Write-Host "    exists, skipping."
    }
}

Write-Host ""
Write-Host "Done. Verify in the Purview compliance portal. Align the regex info"
Write-Host "types in dlp-policy.yaml with custom SIT rule packages where marked TODO."
