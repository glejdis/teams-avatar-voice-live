// ─────────────────────────────────────────────────────────────────────────────
// Agent RBAC module — governance Phase 5 (least privilege)
//
// Assigns each agent identity ONLY the Azure roles declared for it in
// governance/agent-registry.yaml (least_privilege.azure_roles), scoped narrowly
// to the specific resource each role applies to. This replaces broad, shared
// access with per-agent least privilege.
//
//   Azure AI User            -> Foundry (Cognitive Services) account
//   Cognitive Services User  -> Foundry (Cognitive Services) account
//   Storage Blob Data Reader -> deploy storage account (optional)
//   Key Vault Secrets User   -> Key Vault (optional)
//
// principalIds come from infra/modules/agent-identities.bicep outputs. No
// subscription-scoped roles; guid() for deterministic assignment names. Role
// definition IDs match infra/modules/rbac.bicep.
// ─────────────────────────────────────────────────────────────────────────────

@description('''
Per-agent role assignment input. One entry per registered agent:
  - id:          registry agent id
  - principalId: the agent identity (UAMI) principalId
  - azureRoles:  least_privilege.azure_roles from the registry
''')
param agents array

@description('Foundry (Cognitive Services) account name in this RG.')
param foundryAccountName string

@description('Deploy storage account name. Empty = skip Storage assignments.')
param deployStorageAccountName string = ''

@description('Key Vault name. Empty = skip Key Vault assignments.')
param keyVaultName string = ''

// Built-in role definition IDs (match infra/modules/rbac.bicep).
var roleAzureAIUser           = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var roleCognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var roleStorageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var roleKvSecretsUser         = '4633458b-17de-408a-b874-0445c86b69e6'

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = if (!empty(deployStorageAccountName)) {
  name: deployStorageAccountName
}

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' existing = if (!empty(keyVaultName)) {
  name: keyVaultName
}

resource raAzureAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for agent in agents: if (contains(agent.azureRoles, 'Azure AI User')) {
    name: guid(foundry.id, agent.principalId, roleAzureAIUser)
    scope: foundry
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAzureAIUser)
      principalId: agent.principalId
      principalType: 'ServicePrincipal'
      description: 'Agent ${agent.id}: Azure AI User (least-privilege, from agent-registry).'
    }
  }
]

resource raCognitiveServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for agent in agents: if (contains(agent.azureRoles, 'Cognitive Services User')) {
    name: guid(foundry.id, agent.principalId, roleCognitiveServicesUser)
    scope: foundry
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesUser)
      principalId: agent.principalId
      principalType: 'ServicePrincipal'
      description: 'Agent ${agent.id}: Cognitive Services User (least-privilege, from agent-registry).'
    }
  }
]

resource raStorageBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for agent in agents: if (!empty(deployStorageAccountName) && contains(agent.azureRoles, 'Storage Blob Data Reader')) {
    name: guid(storage.id, agent.principalId, roleStorageBlobDataReader)
    scope: storage
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
      principalId: agent.principalId
      principalType: 'ServicePrincipal'
      description: 'Agent ${agent.id}: Storage Blob Data Reader (least-privilege, from agent-registry).'
    }
  }
]

resource raKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for agent in agents: if (!empty(keyVaultName) && contains(agent.azureRoles, 'Key Vault Secrets User')) {
    name: guid(kv.id, agent.principalId, roleKvSecretsUser)
    scope: kv
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsUser)
      principalId: agent.principalId
      principalType: 'ServicePrincipal'
      description: 'Agent ${agent.id}: Key Vault Secrets User (least-privilege, from agent-registry).'
    }
  }
]
