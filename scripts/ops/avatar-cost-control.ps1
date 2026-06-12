# ============================================================================
# avatar-cost-control.ps1 — Daily evening/morning toggles for the avatar stack
# ----------------------------------------------------------------------------
# Usage:
#   .\avatar-cost-control.ps1 -Action Shutdown   # run before logging off
#   .\avatar-cost-control.ps1 -Action Startup    # run when you start the day
#   .\avatar-cost-control.ps1 -Action CostCheck  # quick "what did I spend today"
#
# Why each step exists:
#   - Foundry public-access toggle: stops the Standard Streaming Minute meter
#     that runs whenever the avatar endpoint is reachable, even if no caller
#     is connected. Largest single contributor to overnight cost.
#   - VMSS deallocate: VM compute saving for the avatar host.
#   - Bastion: not toggled here — too destructive. Delete manually if not
#     used for >3 days.
#
# All identifiers below are passed in as parameters — fill them in from
# your deployment's `bicepparam` file, or set the *_DEFAULT env vars below
# so you don't have to re-type them every time.
# ============================================================================

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Shutdown','Startup','CostCheck')]
    [string]$Action,

    [string]$ResourceGroup    = $env:AVATAR_COST_RG,
    [string]$VmssName         = $env:AVATAR_COST_VMSS,
    [string]$FoundryName      = $env:AVATAR_COST_FOUNDRY,
    [string]$SubscriptionId   = $env:AVATAR_COST_SUBSCRIPTION_ID
)

$ErrorActionPreference = 'Stop'

# Validate — fail loudly rather than calling Azure with empty/placeholder values.
$missing = @()
if ([string]::IsNullOrWhiteSpace($ResourceGroup))  { $missing += '-ResourceGroup (or $env:AVATAR_COST_RG)' }
if ([string]::IsNullOrWhiteSpace($VmssName))       { $missing += '-VmssName (or $env:AVATAR_COST_VMSS)' }
if ([string]::IsNullOrWhiteSpace($FoundryName))    { $missing += '-FoundryName (or $env:AVATAR_COST_FOUNDRY)' }
if ([string]::IsNullOrWhiteSpace($SubscriptionId)) { $missing += '-SubscriptionId (or $env:AVATAR_COST_SUBSCRIPTION_ID)' }
if ($missing.Count -gt 0) {
    Write-Host "ERROR: missing required input(s):" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host "Tip: export AVATAR_COST_* env vars in your shell profile so you only set them once." -ForegroundColor Yellow
    exit 1
}

# Aliases kept for readability inside the switch block below.
$RG      = $ResourceGroup
$VMSS    = $VmssName
$FOUNDRY = $FoundryName
$SUBID   = $SubscriptionId

function Invoke-CostQuery {
    $body = '{\"type\":\"Usage\",\"timeframe\":\"MonthToDate\",\"dataset\":{\"granularity\":\"Daily\",\"aggregation\":{\"c\":{\"name\":\"PreTaxCost\",\"function\":\"Sum\"}},\"filter\":{\"dimensions\":{\"name\":\"Meter\",\"operator\":\"In\",\"values\":[\"Standard Streaming Minute\"]}}}}'
    $url  = "https://management.azure.com/subscriptions/$SUBID/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    $resp = az rest --method post --url $url --body $body --headers "Content-Type=application/json" | ConvertFrom-Json
    Write-Host "`n=== Standard Streaming Minute (avatar) — month to date ===" -ForegroundColor Cyan
    if (-not $resp.properties.rows) {
        Write-Host "  (no streaming usage this month — leak fully stopped)" -ForegroundColor Green
        return
    }
    $resp.properties.rows | ForEach-Object {
        $cost = [math]::Round($_[0], 2)
        $date = $_[1].ToString()
        $date = "$($date.Substring(0,4))-$($date.Substring(4,2))-$($date.Substring(6,2))"
        $color = if ($cost -gt 50) { 'Red' } elseif ($cost -gt 10) { 'Yellow' } else { 'Green' }
        Write-Host ("  {0}   ${1,8:N2} USD" -f $date, $cost) -ForegroundColor $color
    }
}

switch ($Action) {

    'Shutdown' {
        Write-Host "`n>>> EVENING SHUTDOWN <<<" -ForegroundColor Yellow

        Write-Host "`n[1/3] Disabling $FOUNDRY public network access (kills streaming meter)..." -ForegroundColor Cyan
        az resource update -g $RG -n $FOUNDRY `
            --resource-type Microsoft.CognitiveServices/accounts `
            --set properties.publicNetworkAccess=Disabled `
            --output none
        $state = az cognitiveservices account show -g $RG -n $FOUNDRY --query "properties.publicNetworkAccess" -o tsv
        Write-Host "      publicNetworkAccess = $state" -ForegroundColor Green

        Write-Host "`n[2/3] Deallocating VMSS instance 0..." -ForegroundColor Cyan
        az vmss deallocate -g $RG --name $VMSS --instance-ids 0 --no-wait
        Write-Host "      deallocate queued (--no-wait); finishes in 1-2 min" -ForegroundColor Green

        Write-Host "`n[3/3] Cost check — confirm streaming meter is/will-be flat" -ForegroundColor Cyan
        Invoke-CostQuery

        Write-Host "`n>>> MANUAL: close all browser tabs (candidate UI / Speech Studio / avatar preview)" -ForegroundColor Yellow
        Write-Host ">>> Re-run with -Action CostCheck in 10 min to verify the meter stopped climbing.`n" -ForegroundColor Yellow
    }

    'Startup' {
        Write-Host "`n>>> MORNING STARTUP <<<" -ForegroundColor Yellow

        Write-Host "`n[1/3] Re-enabling <FOUNDRY_ACCOUNT_NAME> public network access..." -ForegroundColor Cyan
        az resource update -g $RG -n $FOUNDRY `
            --resource-type Microsoft.CognitiveServices/accounts `
            --set properties.publicNetworkAccess=Enabled `
            --output none
        $state = az cognitiveservices account show -g $RG -n $FOUNDRY --query "properties.publicNetworkAccess" -o tsv
        Write-Host "      publicNetworkAccess = $state" -ForegroundColor Green

        Write-Host "`n[2/3] Starting VMSS instance 0..." -ForegroundColor Cyan
        az vmss start -g $RG --name $VMSS --instance-ids 0 --no-wait
        Write-Host "      start queued (--no-wait); NSSM services up in ~3 min" -ForegroundColor Green

        Write-Host "`n[3/3] Yesterday's streaming spend (verify shutdown worked):" -ForegroundColor Cyan
        Invoke-CostQuery

        Write-Host "`n>>> Wait ~3 min before testing. Then place a baseline call to confirm Lisa works.`n" -ForegroundColor Yellow
    }

    'CostCheck' {
        Invoke-CostQuery
    }
}