// ─────────────────────────────────────────────────────────────────────────────
// Diagnostic settings — pipes platform logs/metrics for the Avatar stack
// resources to the Log Analytics workspace.
// ─────────────────────────────────────────────────────────────────────────────

@description('Log Analytics workspace ID.')
param workspaceId string

@description('Key Vault name.')
param keyVaultName string

@description('VMSS name.')
param vmssName string

@description('App Gateway name. Empty to skip.')
param appGwName string = ''

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' existing = { name: keyVaultName }

resource diagKv 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'avatar-diag'
  scope: kv
  properties: {
    workspaceId: workspaceId
    logs: [
      { categoryGroup: 'audit', enabled: true }
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

resource vmss 'Microsoft.Compute/virtualMachineScaleSets@2024-07-01' existing = { name: vmssName }

resource diagVmss 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'avatar-diag'
  scope: vmss
  properties: {
    workspaceId: workspaceId
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

resource appgw 'Microsoft.Network/applicationGateways@2024-01-01' existing = if (!empty(appGwName)) {
  name: appGwName
}

resource diagAppGw 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(appGwName)) {
  name: 'avatar-diag'
  scope: appgw
  properties: {
    workspaceId: workspaceId
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}
