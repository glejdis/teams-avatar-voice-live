#Requires -Version 7.0
<#
.SYNOPSIS
    Wire the AGENT_AUDIT stream to Application Insights / Log Analytics and turn
    on Microsoft Defender coverage for the avatar agent (governance Phase 5).

.DESCRIPTION
    Two parts:

    1) AUDIT ROUTING (scriptable):
       - ensures an Application Insights component exists (linked to the audit
         Log Analytics workspace from infra/modules/audit-sink.bicep) and prints
         its connection string. Set this as APPLICATIONINSIGHTS_CONNECTION_STRING
         for the hosted-agent container (and the browser-fallback host) so
         `agentgov.security.audit.emit` (Python logging) flows into the
         workspace's AgentAudit_CL / traces.

    2) DEFENDER (mostly portal / preview — guided):
       - prints the steps to enable Microsoft Defender for Cloud Apps + the
         Defender "AI / agents" coverage and connect it to the workspace.

    Requirements: Azure CLI signed in; Security Admin for the Defender steps.

.EXAMPLE
    ./02-defender-enable.ps1 -ResourceGroup TVA-RG -WorkspaceName tva-audit -AppInsightsName tva-agents-ai
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ResourceGroup,
    [Parameter(Mandatory)] [string] $WorkspaceName,
    [string] $AppInsightsName = 'tva-agents-ai',
    [string] $Location = 'swedencentral'
)

$ErrorActionPreference = 'Stop'

az extension add --name application-insights --only-show-errors 2>$null | Out-Null

$workspaceId = az monitor log-analytics workspace show `
    -g $ResourceGroup -n $WorkspaceName --query id -o tsv

Write-Host "==> Ensuring Application Insights '$AppInsightsName' (workspace-based)..."
$existing = az monitor app-insights component show -g $ResourceGroup -a $AppInsightsName --query id -o tsv 2>$null
if (-not $existing) {
    az monitor app-insights component create `
        -g $ResourceGroup -a $AppInsightsName -l $Location `
        --workspace $workspaceId --application-type web --only-show-errors | Out-Null
}

$connString = az monitor app-insights component show `
    -g $ResourceGroup -a $AppInsightsName --query connectionString -o tsv

Write-Host ""
Write-Host "AUDIT ROUTING — set this on the hosted-agent container + browser-fallback host:"
Write-Host "  APPLICATIONINSIGHTS_CONNECTION_STRING=$connString"
Write-Host ""
Write-Host "Then enable OpenTelemetry export in the apps, e.g.:"
Write-Host "  pip install azure-monitor-opentelemetry"
Write-Host "  from azure.monitor.opentelemetry import configure_azure_monitor; configure_azure_monitor()"
Write-Host "  -> agentgov.security AGENT_AUDIT logs land in the workspace (traces / AgentAudit_CL)."
Write-Host ""
Write-Host "DEFENDER (preview / portal) — manual steps:"
Write-Host "  1. https://security.microsoft.com -> Settings -> Cloud Apps: connect the tenant."
Write-Host "  2. Enable Defender AI / agent monitoring (preview) for the registered agent"
Write-Host "     identity (see governance/agent-registry.yaml entra.identity_name = id-tva-lisa)."
Write-Host "  3. Point Defender + Sentinel at workspace: $WorkspaceName."
Write-Host "  4. The Sentinel rule from infra/modules/audit-sink.bicep alerts on blocked actions."
