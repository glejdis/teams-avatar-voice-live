// ─────────────────────────────────────────────────────────────────────────────
// Multi-Agent System — demo infrastructure (Foundry + models + Storage + AI Search)
// Target region: swedencentral.   Target RG: <RESOURCE_GROUP> (already exists).
// Deploy with:
//   az deployment group create -g <RESOURCE_GROUP> -f infra/main.bicep
// ─────────────────────────────────────────────────────────────────────────────

@description('Short prefix for resource names (lowercase).')
param prefix string = '<PREFIX>'

@description('Location for all resources.')
param location string = resourceGroup().location

@description('Foundry AI Services account name.')
param foundryAccountName string = '${prefix}-foundry'

@description('Foundry project name (displayed inside AI Foundry portal).')
param foundryProjectName string = '${prefix}-project'

@description('Storage account name (must be globally unique, 3-24 lowercase).')
param storageAccountName string = take('${prefix}pol${uniqueString(resourceGroup().id)}', 24)

@description('Blob container name for HR policy PDFs.')
param blobContainerName string = 'hr-policies'

@description('Optional principalId of the GitHub Actions OIDC UAMI. When set, gets Storage Blob Data Contributor on the storage account so the agent-deploy workflow can upload artifacts via AAD (--auth-mode login).')
param deployerPrincipalId string = ''

@description('Name of the Azure Table that stores per-call cost records (read by the costboard dashboard).')
param costTableName string = 'callcosts'

@description('Extra principalIds to grant Storage Table Data Contributor on the cost table (e.g. the costboard dashboard identity, the VMSS user-assigned identity, or a local developer objectId). The deployer principal (when set) is granted automatically.')
param costStorePrincipalIds array = []

@description('Azure AI Search service name.')
param searchServiceName string = '${prefix}-search-${uniqueString(resourceGroup().id)}'

@description('Azure AI Search SKU.')
@allowed(['basic', 'standard', 'standard2', 'standard3'])
param searchSku string = 'basic'

// ─── Model deployments ───
// Capacity is in thousands of TPM (Tokens-Per-Minute) for OpenAI models on Foundry.
@description('Capacity (K TPM) for gpt-4.1-mini (primary chat model).')
param chatModelCapacity int = 50

@description('Capacity (K TPM) for o4-mini.')
param reasoningModelCapacity int = 50

@description('Capacity (K TPM) for text-embedding-3-large.')
param embeddingModelCapacity int = 50

// ─────────────────────────────────────────────────────────────────────────────
// Foundry (AI Services) account
// ─────────────────────────────────────────────────────────────────────────────
resource foundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: foundryAccountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryAccountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Foundry project (child of the AIServices account)
// ─────────────────────────────────────────────────────────────────────────────
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: foundry
  name: foundryProjectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: foundryProjectName
    description: 'Multi-Agent System demo project'
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Model deployments (deployed sequentially via dependsOn so Foundry respects
// quota ordering).
// ─────────────────────────────────────────────────────────────────────────────
resource chatModel 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: 'gpt-4.1-mini'
  sku: {
    name: 'GlobalStandard'
    capacity: chatModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

resource reasoningModel 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: 'o4-mini'
  sku: {
    name: 'GlobalStandard'
    capacity: reasoningModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'o4-mini'
      version: '2025-04-16'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    chatModel
  ]
}

resource embeddingModel 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: 'text-embedding-3-large'
  sku: {
    name: 'Standard'
    capacity: embeddingModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    reasoningModel
  ]
}

// ─────────────────────────────────────────────────────────────────────────────
// Storage account (HR policy PDFs)
// ─────────────────────────────────────────────────────────────────────────────
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: true
    // Public network access is required so the GitHub Actions runner can push
    // Lisa artifacts via AAD (--auth-mode login). Anonymous access stays
    // disabled (allowBlobPublicAccess=false). Production deployments running
    // a self-hosted runner inside the VNet should set this to 'Disabled'.
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}

resource hrPoliciesContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: blobContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Container for VMSS bootstrap artifacts (bot zip + tools-cache). Consumed by
// scripts/vmss/install.ps1 and the agent-deploy GitHub workflow.
resource artifactsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'agent-artifacts'
  properties: {
    publicAccess: 'None'
  }
}

// Storage Blob Data Contributor for the GitHub Actions OIDC UAMI.
// Required for `az storage blob ... --auth-mode login` from the agent-deploy
// workflow runner. Skip when deployerPrincipalId is empty (e.g. local dev).
var roleStorageBlobDataContributorId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource raDeployerBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(storage.id, deployerPrincipalId, roleStorageBlobDataContributorId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataContributorId)
    principalId: deployerPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Cost telemetry table (per-call cost records, written by every transport's
// CostSink in core/cost.py and read by the costboard dashboard).
// ─────────────────────────────────────────────────────────────────────────────
resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}

resource costTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: costTableName
  properties: {}
}

// Storage Table Data Contributor — lets the configured identities upsert/read
// cost rows via AAD (Managed Identity / DefaultAzureCredential). Granted to the
// deployer principal (when set) plus any extra principalIds supplied.
var roleStorageTableDataContributorId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var costTablePrincipalIds = union(
  empty(deployerPrincipalId) ? [] : [deployerPrincipalId],
  costStorePrincipalIds
)
resource raCostTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in costTablePrincipalIds: {
  name: guid(storage.id, string(pid), roleStorageTableDataContributorId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageTableDataContributorId)
    principalId: pid
    principalType: 'ServicePrincipal'
  }
}]

// ─────────────────────────────────────────────────────────────────────────────
// Azure AI Search
// ─────────────────────────────────────────────────────────────────────────────
resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchServiceName
  location: location
  sku: {
    name: searchSku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'free'
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http403'
      }
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Role assignments (principal running the apps uses DefaultAzureCredential)
// Grant the current deploying principal Storage Blob Data Contributor +
// Search Index Data Contributor so `az login` users can read/write directly.
// Cognitive Services data-plane access for Foundry is inherited via ARM RBAC
// handled separately after deploy (see deploy.ps1).
// ─────────────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────────────
// Azure Communication Services (Teams meeting bridge for Lisa)
// Data location must be one of: Africa, Asia Pacific, Australia, Brazil,
// Canada, Europe, France, Germany, India, Japan, Korea, Norway, Switzerland,
// UAE, UK, United States.
// ─────────────────────────────────────────────────────────────────────────────

@description('ACS resource name.')
param acsName string = '${prefix}-acs'

@description('ACS data residency location.')
@allowed(['Africa', 'Asia Pacific', 'Australia', 'Brazil', 'Canada', 'Europe', 'France', 'Germany', 'India', 'Japan', 'Korea', 'Norway', 'Switzerland', 'UAE', 'UK', 'United States'])
param acsDataLocation string = 'Germany'

resource acs 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: acsName
  location: 'global'
  properties: {
    dataLocation: acsDataLocation
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Azure Container Registry (Agent container image host)
// ─────────────────────────────────────────────────────────────────────────────

@description('ACR name. 5-50 lowercase alphanumeric, globally unique.')
param acrName string = take('${prefix}acr${uniqueString(resourceGroup().id)}', 50)

@description('Deploy ACR alongside the rest of the stack. Set false if it already exists outside this template.')
param deployAcr bool = true

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = if (deployAcr) {
  name: acrName
  location: location
  sku: { name: 'Standard' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// Foundry project MI needs AcrPull on the registry to pull the Agent container image.
// Per /memories/foundry-hosted-agent-gotchas.md the *project* MI (not account
// MI) is the one that pulls images. Get the principalId from the project
// resource defined above.
var roleAcrPullId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource raProjectAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAcr) {
  scope: acr
  name: guid(acr.id, project.id, roleAcrPullId)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPullId)
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Avatar stack (VMSS + KV + Bastion + Monitor + optional AppGw)
// ─────────────────────────────────────────────────────────────────────────────

@description('Deploy the avatar VMSS stack. Set false to skip while iterating on the Foundry side only.')
param deployAvatarStack bool = false

@description('Local admin password for the avatar VMSS (break-glass via Bastion).')
@secure()
param avatarAdminPassword string = ''

@description('Bootstrap zip URL for the avatar VMSS (uploaded by the agent-deploy workflow).')
param avatarBootstrapZipUrl string = ''

@description('URL to install.ps1 (separate blob, uploaded by the agent-deploy workflow). Required when avatarBootstrapZipUrl is set.')
param avatarInstallScriptUrl string = ''

@description('Install the bot TLS cert from KV onto VMSS instances. Set false on first deploy (before the cert exists), then re-deploy with true.')
param avatarInstallCertFromKeyVault bool = true

@description('Email for avatar stack alerts (empty = no alerts).')
param avatarAlertEmail string = ''

@description('Avatar VMSS instance count.')
param avatarInstanceCount int = 1

module avatarStack 'avatar-stack.bicep' = if (deployAvatarStack) {
  name: 'avatar-stack'
  params: {
    location: location
    namePrefix: '${prefix}-avatar'
    foundryAccountName: foundry.name
    foundryProjectName: project.name
    acrName: deployAcr ? acr.name : acrName
    deployStorageAccountName: storage.name
    bootstrapZipUrl: avatarBootstrapZipUrl
    installScriptUrl: avatarInstallScriptUrl
    installCertFromKeyVault: avatarInstallCertFromKeyVault
    adminPassword: avatarAdminPassword
    vmssInstanceCount: avatarInstanceCount
    alertEmail: avatarAlertEmail
    tags: {
      workload: 'avatar'
      managedBy: 'bicep'
    }
  }
}

// ─── Outputs used by the updater script to fill .env files ───
output foundryEndpoint string = foundry.properties.endpoint
output foundryProjectEndpoint string = '${foundry.properties.endpoint}api/projects/${foundryProjectName}'
output foundryAccountName string = foundry.name
output foundryProjectName string = project.name

output chatDeploymentName string = chatModel.name
output reasoningDeploymentName string = reasoningModel.name
output embeddingDeploymentName string = embeddingModel.name

output storageAccountName string = storage.name
output blobContainerName string = hrPoliciesContainer.name
output storageEndpoint string = storage.properties.primaryEndpoints.blob
output costTableName string = costTable.name
output costTableEndpoint string = storage.properties.primaryEndpoints.table

output searchServiceName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchIndexName string = '<SEARCH_INDEX_NAME>'

output acsName string = acs.name
output acsImmutableResourceId string = acs.properties.immutableResourceId

output acrName string = deployAcr ? acr!.name : acrName
output acrLoginServer string = deployAcr ? acr!.properties.loginServer : ''

output artifactsContainer string = artifactsContainer.name
output vmssName string = deployAvatarStack ? avatarStack!.outputs.vmssName : ''
output keyVaultName string = deployAvatarStack ? avatarStack!.outputs.keyVaultName : ''

output location string = location
output resourceGroupName string = resourceGroup().name
