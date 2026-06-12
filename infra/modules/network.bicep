// ─────────────────────────────────────────────────────────────────────────────
// Network module — Avatar stack
//
// Creates a VNet with four purpose-built subnets:
//   - vmss          : the bot/sidecar VMSS instances (NSG-attached)
//   - appgw         : Application Gateway (when enabled). Reserved /27.
//   - bastion       : Azure Bastion. Must be named exactly 'AzureBastionSubnet',
//                     min /26.
//   - privateLink   : private endpoints (Key Vault, Storage). Reserved /27.
//
// The VMSS NSG default-denies inbound except:
//   - 443 from Internet (Graph calling signaling)
//   - 8445 from Internet (Microsoft.Skype.Bots.Media TCP)
//   - 5001 from VirtualNetwork (sidecar /health probe — internal only)
//
// NO operator-IP allow rules. Ops access is via Azure Bastion.
//
// Standard Load Balancer (when enableLoadBalancer = true) fronts the VMSS:
//   - Public Standard SKU PIP with DNS label.
//   - LB rule 443 -> backend 443 (Graph signaling, healthProbe TCP:443).
//   - Inbound NAT pool 8445-8544 -> backend 8445 (per-instance media).
//     Each VMSS instance gets a unique frontend port = 8445 + instanceId.
//     Standard LB DOES support hairpin loopback for these NAT rules so the
//     Skype Bots Media SDK's TryCheckTcpConnectivity self-test from inside
//     the VM to its OWN public endpoint succeeds. Per-instance public IPs
//     do NOT support hairpin (Azure design) and the SDK can't be fooled by
//     /etc/hosts because it discovers the public IP via IMDS.
// ─────────────────────────────────────────────────────────────────────────────

@description('Region.')
param location string

@description('Common name prefix.')
param namePrefix string

@description('VNet address space.')
param vnetAddressSpace string = '10.42.0.0/16'

@description('VMSS subnet prefix.')
param vmssSubnetPrefix string = '10.42.0.0/24'

@description('App Gateway subnet prefix (must be at least /27).')
param appGwSubnetPrefix string = '10.42.1.0/27'

@description('Azure Bastion subnet prefix (must be at least /26).')
param bastionSubnetPrefix string = '10.42.2.0/26'

@description('Private Endpoint subnet prefix.')
param privateLinkSubnetPrefix string = '10.42.3.0/27'

@description('Provision a Standard Load Balancer + public IP that fronts the VMSS for both Graph signaling (443) and the Skype Bots Media platform (8445 per-instance NAT).')
param enableLoadBalancer bool = true

@description('DNS label for the public load balancer frontend. Becomes <prefix>.<region>.cloudapp.azure.com. Empty = no DNS label (use raw IP).')
param lbDnsLabelPrefix string = '${namePrefix}-lb'

@description('First port in the inbound NAT pool that maps to backend 8445 on each VMSS instance. Frontend port for instance i is mediaNatStartPort + i. Default 8445 — keeps the FIRST instance reachable on the symmetric port 8445 (matches Microsoft Graph calling sample defaults).')
@minValue(1024)
@maxValue(65000)
param mediaNatStartPort int = 8445

@description('Last port in the inbound NAT pool. Pool size caps the maximum supported VMSS instance count.')
@minValue(1024)
@maxValue(65535)
param mediaNatEndPort int = 8544

@description('Tags applied to every resource.')
param tags object = {}

var vnetName = '${namePrefix}-vnet'
var nsgVmssName = '${namePrefix}-vmss-nsg'
var nsgBastionName = '${namePrefix}-bastion-nsg'
var lbName = '${namePrefix}-lb'
var lbPipName = '${namePrefix}-lb-pip'
var lbBackendPoolName = 'vmss-backend'
var lbFrontendName = 'lb-frontend'
var lbProbe443Name = 'probe-tcp-443'
var lbProbe8445Name = 'probe-tcp-8445'
var lbProbe80Name = 'probe-tcp-80'
var lbRule443Name = 'lb-rule-443'
var lbRule80Name = 'lb-rule-80'
var lbNatPoolMediaName = 'nat-pool-media-8445'

// ── VMSS NSG ──
resource nsgVmss 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: nsgVmssName
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        // Inbound calling notifications from Microsoft Graph. The public LB
        // listens on 443 and DNATs to backend port 9442 (Kestrel
        // BotInternalPort), so the NSG sees the post-NAT destination port.
        name: 'AllowBotKestrelHTTPS'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: [
            '9441'
            '9442'
          ]
        }
      }
      {
        name: 'AllowMediaPort8445'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '8445'
        }
      }
      {
        // Port 80 inbound — used ONLY by Let's Encrypt HTTP-01 ACME challenges
        // when renewing the bot TLS cert. There is no production traffic on
        // port 80; the bot itself serves only HTTPS.
        name: 'AllowAcmeHttp01'
        properties: {
          priority: 115
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
      {
        name: 'AllowSidecarHealthFromVnet'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '5001'
        }
      }
      {
        name: 'AllowAppGwBackendFromVnet'
        properties: {
          priority: 130
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '8443'
        }
      }
    ]
  }
}

// ── Bastion NSG (per Bastion required-rules spec) ──
resource nsgBastion 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: nsgBastionName
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowHttpsInbound'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowGatewayManager'
        properties: {
          priority: 130
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'GatewayManager'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowAzureLoadBalancer'
        properties: {
          priority: 140
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowBastionHostCommunication'
        properties: {
          priority: 150
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRanges: [ '8080', '5701' ]
        }
      }
      {
        name: 'AllowSshRdpOutbound'
        properties: {
          priority: 100
          direction: 'Outbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRanges: [ '22', '3389' ]
        }
      }
      {
        name: 'AllowAzureCloudOutbound'
        properties: {
          priority: 110
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: 'AzureCloud'
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowBastionCommunicationOutbound'
        properties: {
          priority: 120
          direction: 'Outbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRanges: [ '8080', '5701' ]
        }
      }
      {
        name: 'AllowGetSessionInformation'
        properties: {
          priority: 130
          direction: 'Outbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: 'Internet'
          destinationPortRange: '80'
        }
      }
    ]
  }
}

// ── VNet ──
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ vnetAddressSpace ] }
    subnets: [
      {
        name: 'vmss'
        properties: {
          addressPrefix: vmssSubnetPrefix
          networkSecurityGroup: { id: nsgVmss.id }
        }
      }
      {
        name: 'appgw'
        properties: {
          addressPrefix: appGwSubnetPrefix
        }
      }
      {
        name: 'AzureBastionSubnet'
        properties: {
          addressPrefix: bastionSubnetPrefix
          networkSecurityGroup: { id: nsgBastion.id }
        }
      }
      {
        name: 'privateLink'
        properties: {
          addressPrefix: privateLinkSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

// ── Standard Load Balancer (fronts VMSS) ──
// Required for the Microsoft Graph calling bot media SDK to pass its
// TryCheckTcpConnectivity self-test: the SDK opens a TCP socket from the VM
// to its OWN public IP:8445 and Azure per-instance Public IPs do NOT support
// hairpin NAT (Azure design). Standard LB DOES support hairpin loopback for
// inbound NAT rules, so the self-test succeeds.
resource lbPip 'Microsoft.Network/publicIPAddresses@2024-01-01' = if (enableLoadBalancer) {
  name: lbPipName
  location: location
  tags: tags
  sku: { name: 'Standard' }
  zones: [ '1', '2', '3' ]
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    idleTimeoutInMinutes: 15
    dnsSettings: empty(lbDnsLabelPrefix) ? null : {
      domainNameLabel: lbDnsLabelPrefix
    }
  }
}

resource lb 'Microsoft.Network/loadBalancers@2024-01-01' = if (enableLoadBalancer) {
  name: lbName
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    frontendIPConfigurations: [
      {
        name: lbFrontendName
        properties: {
          publicIPAddress: { id: lbPip!.id }
        }
      }
    ]
    backendAddressPools: [
      { name: lbBackendPoolName }
    ]
    probes: [
      {
        // Probes Kestrel HTTPS calling listener (BotInternalPort) on the VM.
        // The LB rewrites public 443 -> backend 9442; probe must hit the same
        // backend port to reflect actual bot health.
        name: lbProbe443Name
        properties: {
          protocol: 'Tcp'
          port: 9442
          intervalInSeconds: 15
          numberOfProbes: 2
        }
      }
      {
        name: lbProbe8445Name
        properties: {
          protocol: 'Tcp'
          port: 8445
          intervalInSeconds: 15
          numberOfProbes: 2
        }
      }
      {
        // Port 80 probe for ACME HTTP-01. The probe will report 'unhealthy'
        // when no ACME renewal is in progress — that is expected and harmless.
        // The LB rule for port 80 will simply not forward traffic until a
        // listener (Posh-ACME WebSelfHost) is running, which is exactly what
        // we want: a one-shot challenge endpoint.
        name: lbProbe80Name
        properties: {
          protocol: 'Tcp'
          port: 80
          intervalInSeconds: 15
          numberOfProbes: 2
        }
      }
    ]
    loadBalancingRules: [
      {
        // Graph calling signaling. Public 443 -> backend 9442 (Kestrel
        // BotInternalPort). The bot's calling controller is hosted on this
        // listener (along with /api/messages); /api/calling notifications
        // from Microsoft Graph land here via the LB.
        name: lbRule443Name
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', lbName, lbFrontendName)
          }
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', lbName, lbBackendPoolName)
          }
          probe: {
            id: resourceId('Microsoft.Network/loadBalancers/probes', lbName, lbProbe443Name)
          }
          protocol: 'Tcp'
          frontendPort: 443
          backendPort: 9442
          enableFloatingIP: false
          enableTcpReset: true
          idleTimeoutInMinutes: 15
          loadDistribution: 'Default'
          // Outbound SNAT must be disabled here because the same frontend IP
          // is referenced by an explicit outbound rule below (Azure constraint).
          disableOutboundSnat: true
        }
      }
      {
        // ACME HTTP-01 challenge. Public 80 -> backend 80. Used only when
        // running Posh-ACME's WebSelfHost plugin during cert issuance/renewal.
        // Outside of that window no listener exists and the LB rule simply
        // returns connection refused — acceptable for a POC.
        name: lbRule80Name
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', lbName, lbFrontendName)
          }
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', lbName, lbBackendPoolName)
          }
          probe: {
            id: resourceId('Microsoft.Network/loadBalancers/probes', lbName, lbProbe80Name)
          }
          protocol: 'Tcp'
          frontendPort: 80
          backendPort: 80
          enableFloatingIP: false
          enableTcpReset: true
          idleTimeoutInMinutes: 4
          loadDistribution: 'Default'
          // Outbound SNAT must be disabled here because the same frontend IP
          // is referenced by an explicit outbound rule below (Azure constraint).
          disableOutboundSnat: true
        }
      }
    ]
    // Explicit outbound rule. Required for Microsoft Teams Media SDK calling
    // bots: the Media SDK opens UDP STUN/TURN sessions to *.tr.teams.microsoft.com
    // on port 3478 (and TCP fallbacks). Standard LB without an explicit outbound
    // rule does NOT SNAT outbound UDP, so ICE connectivity checks fail with
    // "TransportEndpoint ConnectivityChecksCompleted (reason: OperationFailed)"
    // and the bot drops out of the call. Allocating ports per instance with
    // Protocol=All covers both TCP and UDP egress.
    outboundRules: [
      {
        name: 'vmss-outbound'
        properties: {
          frontendIPConfigurations: [
            {
              id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', lbName, lbFrontendName)
            }
          ]
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', lbName, lbBackendPoolName)
          }
          protocol: 'All'
          allocatedOutboundPorts: 0      // 0 = automatic per-backend allocation
          idleTimeoutInMinutes: 15
          enableTcpReset: true
        }
      }
    ]
    inboundNatPools: [
      {
        // Per-instance media. Frontend port for instance i = mediaNatStartPort + i,
        // forwarded to backend 8445. The bot's appsettings sets
        // InstancePublicPort = mediaNatStartPort + instanceId; the VMSS sample
        // shows how to compute instanceId from the platform metadata.
        name: lbNatPoolMediaName
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', lbName, lbFrontendName)
          }
          protocol: 'Tcp'
          frontendPortRangeStart: mediaNatStartPort
          frontendPortRangeEnd: mediaNatEndPort
          backendPort: 8445
          enableFloatingIP: false
          enableTcpReset: true
          idleTimeoutInMinutes: 15
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output vmssSubnetId string = '${vnet.id}/subnets/vmss'
output appGwSubnetId string = '${vnet.id}/subnets/appgw'
output bastionSubnetId string = '${vnet.id}/subnets/AzureBastionSubnet'
output privateLinkSubnetId string = '${vnet.id}/subnets/privateLink'
output lbBackendPoolId string = enableLoadBalancer ? '${lb!.id}/backendAddressPools/${lbBackendPoolName}' : ''
output lbMediaNatPoolId string = enableLoadBalancer ? '${lb!.id}/inboundNatPools/${lbNatPoolMediaName}' : ''
output lbPublicIp string = enableLoadBalancer ? lbPip!.properties.ipAddress : ''
output lbFqdn string = enableLoadBalancer && !empty(lbDnsLabelPrefix) ? lbPip!.properties.dnsSettings.fqdn : ''
output mediaNatStartPort int = mediaNatStartPort
