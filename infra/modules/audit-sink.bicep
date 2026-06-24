// ─────────────────────────────────────────────────────────────────────────────
// Audit sink — routes the agent's AGENT_AUDIT stream to Log Analytics + Sentinel
// (governance Phase 5: observability).
//
//   - Log Analytics workspace (or bring your own)
//   - a custom `AgentAudit_CL` table for the structured AuditEvent records
//   - Microsoft Sentinel onboarding on that workspace
//   - a scheduled analytics rule that alerts on DLP-block / prompt-injection /
//     blocked decisions emitted by agentgov.security
//
// The agent emits AGENT_AUDIT via Python logging. Set
// APPLICATIONINSIGHTS_CONNECTION_STRING on the hosted-agent container (and the
// browser-fallback host) so those log lines flow App Insights -> this workspace,
// where the AgentAudit_CL table + Sentinel rule make them queryable + alertable.
// ─────────────────────────────────────────────────────────────────────────────

@description('Location for the workspace.')
param location string = resourceGroup().location

@description('Log Analytics workspace name.')
param workspaceName string

@description('Retention (days) for the audit data.')
@minValue(30)
param retentionInDays int = 90

@description('Enable Microsoft Sentinel + the analytics rule on the workspace.')
param enableSentinel bool = true

@description('Resource tags.')
param tags object = {}

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource auditTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: 'AgentAudit_CL'
  properties: {
    retentionInDays: retentionInDays
    schema: {
      name: 'AgentAudit_CL'
      columns: [
        { name: 'TimeGenerated', type: 'datetime' }
        { name: 'correlationId_g', type: 'string' }
        { name: 'agentId_s', type: 'string' }
        { name: 'action_s', type: 'string' }
        { name: 'direction_s', type: 'string' }
        { name: 'userOid_g', type: 'string' }
        { name: 'userMail_s', type: 'string' }
        { name: 'userResolved_b', type: 'boolean' }
        { name: 'classification_s', type: 'string' }
        { name: 'dlpVerdict_s', type: 'string' }
        { name: 'dlpFindingTypes_s', type: 'string' }
        { name: 'injectionDetected_b', type: 'boolean' }
        { name: 'blockReason_s', type: 'string' }
        { name: 'decision_s', type: 'string' }
      ]
    }
  }
}

resource sentinel 'Microsoft.SecurityInsights/onboardingStates@2023-11-01' = if (enableSentinel) {
  scope: workspace
  name: 'default'
  properties: {}
}

resource blockedActionRule 'Microsoft.SecurityInsights/alertRules@2023-11-01' = if (enableSentinel) {
  scope: workspace
  name: guid(workspace.id, 'agent-audit-blocked')
  kind: 'Scheduled'
  properties: {
    displayName: 'Governed agent — blocked action (DLP / prompt injection)'
    description: 'A governed agent blocked an action: DLP verdict=block or a prompt-injection signal.'
    severity: 'Medium'
    enabled: true
    query: 'AgentAudit_CL\n| where decision_s == "blocked" or dlpVerdict_s == "block" or injectionDetected_b == true'
    queryFrequency: 'PT1H'
    queryPeriod: 'PT1H'
    triggerOperator: 'GreaterThan'
    triggerThreshold: 0
    suppressionDuration: 'PT1H'
    suppressionEnabled: false
    tactics: [
      'Exfiltration'
      'InitialAccess'
    ]
  }
  dependsOn: [
    sentinel
  ]
}

output workspaceId string = workspace.id
output workspaceCustomerId string = workspace.properties.customerId
