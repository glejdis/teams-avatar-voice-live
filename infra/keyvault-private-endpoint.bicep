// -----------------------------------------------------------------------------
// Focused Key Vault private endpoint deployment - production recovery
//
// Use this when we want to fix only the Key Vault private access path without
// touching VMSS, bot, Foundry, storage, or ACS browser demo resources.
// -----------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Region.')
param location string = resourceGroup().location

@description('Common Avatar name prefix.')
param namePrefix string = '<NAME_PREFIX>'

@description('Existing Key Vault name.')
param keyVaultName string = '<KEY_VAULT_NAME>'

@description('Existing Avatar VNet name.')
param vnetName string = '<VNET_NAME>'

@description('Existing subnet name for private endpoints.')
param privateLinkSubnetName string = 'privateLink'

@description('Private DNS zone name for Azure Key Vault private endpoints.')
param privateDnsZoneName string = 'privatelink.vaultcore.azure.net'

@description('Resource tags.')
param tags object = {
  workload: 'avatar'
  managedBy: 'bicep'
  env: 'prod'
}

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' existing = {
  name: keyVaultName
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' existing = {
  name: vnetName
}

module kvPrivateEndpoint 'modules/keyvault-private-endpoint.bicep' = {
  name: 'avatar-kv-private-endpoint'
  params: {
    location: location
    namePrefix: namePrefix
    keyVaultId: kv.id
    keyVaultName: kv.name
    privateEndpointSubnetId: '${vnet.id}/subnets/${privateLinkSubnetName}'
    vnetId: vnet.id
    privateDnsZoneName: privateDnsZoneName
    tags: tags
  }
}

output keyVaultPrivateEndpointId string = kvPrivateEndpoint.outputs.privateEndpointId
output keyVaultPrivateDnsZoneId string = kvPrivateEndpoint.outputs.privateDnsZoneId
