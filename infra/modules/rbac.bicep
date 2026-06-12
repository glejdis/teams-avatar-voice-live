// ─────────────────────────────────────────────────────────────────────────────
// RBAC module — Avatar stack
//
// Grants the VMSS instance MI the access it needs:
//   - AcrPull                              on the ACR (pull lisa container)
//   - Key Vault Secrets User               on the KV (read bot-aad-secret +
//                                          tls cert via AKV VM extension)
//   - Cognitive Services User              on the Foundry account (call models)
//   - Azure AI User                        on the Foundry account (use agent)
//   - Storage Blob Data Reader             on the deploy storage account
//                                          (download bootstrap zip, read state)
//
// All assignments are scoped narrowly to the target resource. No subscription-
// scoped roles. Uses guid() for deterministic assignment names.
// ─────────────────────────────────────────────────────────────────────────────

@description('VMSS principal ID (system-assigned MI).')
param vmssPrincipalId string

@description('Existing Key Vault name in the same RG.')
param keyVaultName string

@description('Existing Foundry (Cognitive Services) account name in the same RG.')
param foundryAccountName string

@description('Existing ACR name. Empty = skip.')
param acrName string = ''

@description('Existing storage account name (deploy artifacts). Empty = skip.')
param deployStorageAccountName string = ''

// Built-in role definition IDs.
var roleAcrPull               = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleKvSecretsUser         = '4633458b-17de-408a-b874-0445c86b69e6'
var roleCognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var roleAzureAIUser           = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var roleStorageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' existing = {
  name: keyVaultName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = if (!empty(acrName)) {
  name: acrName
}

resource deployStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = if (!empty(deployStorageAccountName)) {
  name: deployStorageAccountName
}

resource raKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, vmssPrincipalId, roleKvSecretsUser)
  properties: {
    principalId: vmssPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsUser)
  }
}

resource raCognitiveServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, vmssPrincipalId, roleCognitiveServicesUser)
  properties: {
    principalId: vmssPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesUser)
  }
}

resource raAzureAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, vmssPrincipalId, roleAzureAIUser)
  properties: {
    principalId: vmssPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAzureAIUser)
  }
}

resource raAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(acrName)) {
  scope: acr
  name: guid(resourceId('Microsoft.ContainerRegistry/registries', acrName), vmssPrincipalId, roleAcrPull)
  properties: {
    principalId: vmssPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}

resource raStorageBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployStorageAccountName)) {
  scope: deployStorage
  name: guid(resourceId('Microsoft.Storage/storageAccounts', deployStorageAccountName), vmssPrincipalId, roleStorageBlobDataReader)
  properties: {
    principalId: vmssPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
  }
}
