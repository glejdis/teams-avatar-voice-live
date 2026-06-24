// ─────────────────────────────────────────────────────────────────────────────
// Governance stack — provisions governed agent identities + least-privilege
// RBAC + the AGENT_AUDIT sink (governance Phase 5).
//
// Composes the rollout modules into one deployable stack:
//   - agent-identities.bicep : one User-Assigned MI per registered agent
//   - agent-rbac.bicep       : least-privilege role assignments per agent
//   - audit-sink.bicep       : Log Analytics + AgentAudit_CL table + Sentinel
//
// The `agents` parameter is GENERATED from governance/agent-registry.yaml by
// governance/generate_bicep_params.py -> infra/params/agent365.params.json
// (entries carry id + identityName + azureRoles).
//
//   az deployment group create -g <rg> -f infra/agent365.bicep \
//     -p @infra/params/agent365.params.json \
//     -p foundryAccountName=<foundry> workspaceName=<la-workspace> \
//        deployStorageAccountName=<sa> keyVaultName=<kv>
// ─────────────────────────────────────────────────────────────────────────────

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('''
Registered agents (generated from the registry). Each entry:
  - id, identityName, azureRoles
''')
param agents array

@description('Foundry (Cognitive Services) account name for AI role assignments.')
param foundryAccountName string

@description('Deploy storage account name. Empty = skip Storage role assignments.')
param deployStorageAccountName string = ''

@description('Key Vault name. Empty = skip Key Vault role assignments.')
param keyVaultName string = ''

@description('Log Analytics workspace name for the AGENT_AUDIT sink.')
param workspaceName string

@description('Provision the audit sink (Log Analytics + Sentinel). Set false to skip.')
param deployAuditSink bool = true

@description('Enable Microsoft Sentinel + the blocked-action analytics rule.')
param enableSentinel bool = true

module identities 'modules/agent-identities.bicep' = {
  name: 'agent-identities'
  params: {
    location: location
    tags: tags
    agents: [for a in agents: {
      id: a.id
      identityName: a.identityName
    }]
  }
}

module rbac 'modules/agent-rbac.bicep' = {
  name: 'agent-rbac'
  params: {
    foundryAccountName: foundryAccountName
    deployStorageAccountName: deployStorageAccountName
    keyVaultName: keyVaultName
    agents: [for (a, i) in agents: {
      id: a.id
      principalId: identities.outputs.identities[i].principalId
      azureRoles: a.azureRoles
    }]
  }
}

module auditSink 'modules/audit-sink.bicep' = if (deployAuditSink) {
  name: 'agent-audit-sink'
  params: {
    location: location
    workspaceName: workspaceName
    enableSentinel: enableSentinel
    tags: tags
  }
}

output identities array = identities.outputs.identities
output auditWorkspaceId string = deployAuditSink ? auditSink!.outputs.workspaceId : ''
