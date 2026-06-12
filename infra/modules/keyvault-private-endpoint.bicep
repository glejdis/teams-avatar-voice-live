// -----------------------------------------------------------------------------
// Key Vault private endpoint module - Avatar stack
//
// Creates a private endpoint for the vault in the privateLink subnet, creates
// or owns the privatelink.vaultcore.azure.net private DNS zone in this resource
// group, links that zone to the Avatar VNet, and attaches the zone group to the
// private endpoint so the vault A record is managed by Azure.
// -----------------------------------------------------------------------------

@description('Region.')
param location string

@description('Common name prefix.')
param namePrefix string

@description('Existing Key Vault resource ID.')
param keyVaultId string

@description('Existing Key Vault name.')
param keyVaultName string

@description('Subnet ID for private endpoints. Use the Avatar privateLink subnet, not the VMSS subnet.')
param privateEndpointSubnetId string

@description('Avatar VNet resource ID to link to the private DNS zone.')
param vnetId string

@description('Private DNS zone name for Azure Key Vault private endpoints.')
param privateDnsZoneName string = 'privatelink.vaultcore.azure.net'

@description('Resource tags.')
param tags object = {}

var privateEndpointName = '${namePrefix}-kv-pe'
var privateEndpointConnectionName = '${keyVaultName}-vault'
var privateDnsZoneGroupName = 'default'
var vnetLinkName = '${namePrefix}-kvdns-link'

resource vaultPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource vaultPrivateDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: vaultPrivateDnsZone
  name: vnetLinkName
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource vaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: privateEndpointName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: privateEndpointConnectionName
        properties: {
          privateLinkServiceId: keyVaultId
          groupIds: [
            'vault'
          ]
          privateLinkServiceConnectionState: {
            status: 'Approved'
            description: 'Auto-approved by Avatar stack deployment.'
            actionsRequired: 'None'
          }
        }
      }
    ]
  }
}

resource vaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: vaultPrivateEndpoint
  name: privateDnsZoneGroupName
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: vaultPrivateDnsZone.id
        }
      }
    ]
  }
  dependsOn: [
    vaultPrivateDnsVnetLink
  ]
}

output privateEndpointId string = vaultPrivateEndpoint.id
output privateEndpointName string = vaultPrivateEndpoint.name
output privateDnsZoneId string = vaultPrivateDnsZone.id
output privateDnsZoneName string = vaultPrivateDnsZone.name
