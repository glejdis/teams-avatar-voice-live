// ─────────────────────────────────────────────────────────────────────────────
// Key Vault module — Avatar stack
//
// RBAC-mode Key Vault (legacy access policies disabled). Stores:
//   - bot-aad-secret           : the Graph calling bot's AAD client secret
//   - bot-tls-cert             : (optional) PFX served by the bot when AppGw
//                                is NOT used. KV-managed cert, auto-rotates.
//
// VMSS instance MI is granted `Key Vault Secrets User` separately by the rbac
// module. Workflow runners (deploy UAMI) get `Key Vault Secrets Officer` to
// rotate secrets.
//
// Production should use publicNetworkAccess = Disabled with a private endpoint
// and privatelink.vaultcore.azure.net private DNS link.
// ─────────────────────────────────────────────────────────────────────────────

@description('Region.')
param location string

@description('Key Vault name. 3-24 chars, globally unique.')
param keyVaultName string

@description('Tenant ID for KV.')
param tenantId string = subscription().tenantId

@description('Soft-delete retention (days, 7-90).')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 90

@description('Enable purge protection (cannot be disabled once enabled).')
param enablePurgeProtection bool = true

@description('Resource tags.')
param tags object = {}

@allowed([ 'Enabled', 'Disabled' ])
@description('Key Vault public network access. Production should use Disabled and private endpoint access.')
param publicNetworkAccess string = 'Enabled'

@allowed([ 'Allow', 'Deny' ])
@description('Default action for Key Vault network ACLs. Production should use Deny when public network access is ever re-enabled.')
param networkAclsDefaultAction string = 'Allow'

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: publicNetworkAccess
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: networkAclsDefaultAction
    }
  }
}

output keyVaultId string = kv.id
output keyVaultName string = kv.name
output keyVaultUri string = kv.properties.vaultUri
