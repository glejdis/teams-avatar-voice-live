// ─────────────────────────────────────────────────────────────────────────────
// Bastion module — Avatar stack
//
// Azure Bastion (Basic SKU) for operator RDP access to the VMSS instances.
// Eliminates public RDP entirely — there is no NSG rule for RDP from the
// internet. Operators connect via the Azure Portal → "Connect via Bastion".
// ─────────────────────────────────────────────────────────────────────────────

@description('Region.')
param location string

@description('Common name prefix.')
param namePrefix string

@description('AzureBastionSubnet ID (must be at least /26).')
param bastionSubnetId string

@description('Resource tags.')
param tags object = {}

var bastionName = '${namePrefix}-bastion'
var pipName = '${namePrefix}-bastion-pip'

resource pip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: pipName
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2024-01-01' = {
  name: bastionName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    ipConfigurations: [
      {
        name: 'IpConf'
        properties: {
          subnet: { id: bastionSubnetId }
          publicIPAddress: { id: pip.id }
        }
      }
    ]
  }
}

output bastionId string = bastion.id
output bastionName string = bastion.name
