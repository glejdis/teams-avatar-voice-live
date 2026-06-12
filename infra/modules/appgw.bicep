// ─────────────────────────────────────────────────────────────────────────────
// Application Gateway module — Avatar stack (OPTIONAL: signaling only)
//
// AppGw_v2 with WAF terminates TLS for the Microsoft Graph SIGNALING port (443)
// and proxies to the VMSS backend. The KV-managed cert auto-rotates — no more
// thumbprint-pinning in appsettings.json.
//
// IMPORTANT: AppGw is L7 only. The bot's MEDIA port (8445) is raw TCP and is
// NOT proxied here — it stays exposed via the per-instance public IPs created
// in vmss.bicep. That's a Microsoft.Skype.Bots.Media constraint, not ours.
// ─────────────────────────────────────────────────────────────────────────────

@description('Region.')
param location string

@description('Common name prefix.')
param namePrefix string

@description('AppGw subnet ID.')
param subnetId string

@description('Backend HTTPS port on the VMSS instances (signaling). Default 443.')
param backendPort int = 443

@description('Backend protocol (Http or Https).')
@allowed([ 'Http', 'Https' ])
param backendProtocol string = 'Https'

@description('User-assigned MI ID that has KV Secrets User on the cert source vault.')
param userAssignedIdentityId string

@description('KV secret URI of the PFX certificate (versionless URI recommended for auto-rotation). NOT the secret value — this is a reference URI.')
#disable-next-line secure-parameter-default
param kvCertId string

@description('Public DNS label for the AppGw frontend IP.')
param dnsLabelPrefix string

@description('Resource tags.')
param tags object = {}

var appGwName = '${namePrefix}-appgw'
var pipName = '${appGwName}-pip'
var wafPolicyName = '${appGwName}-waf'

resource pip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: pipName
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: {
      domainNameLabel: dnsLabelPrefix
    }
  }
}

resource wafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-01-01' = {
  name: wafPolicyName
  location: location
  tags: tags
  properties: {
    policySettings: {
      requestBodyCheck: true
      maxRequestBodySizeInKb: 128
      fileUploadLimitInMb: 100
      state: 'Enabled'
      mode: 'Prevention'
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
        }
      ]
    }
  }
}

resource appGw 'Microsoft.Network/applicationGateways@2024-01-01' = {
  name: appGwName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    sku: { name: 'WAF_v2', tier: 'WAF_v2' }
    autoscaleConfiguration: {
      minCapacity: 1
      maxCapacity: 3
    }
    firewallPolicy: { id: wafPolicy.id }
    sslCertificates: [
      {
        name: 'kvCert'
        properties: {
          keyVaultSecretId: kvCertId
        }
      }
    ]
    gatewayIPConfigurations: [
      {
        name: 'gwip'
        properties: { subnet: { id: subnetId } }
      }
    ]
    frontendIPConfigurations: [
      {
        name: 'feip'
        properties: { publicIPAddress: { id: pip.id } }
      }
    ]
    frontendPorts: [
      { name: 'fe-443', properties: { port: 443 } }
      { name: 'fe-80', properties: { port: 80 } }
    ]
    backendAddressPools: [
      { name: 'vmss-pool', properties: {} }
    ]
    backendHttpSettingsCollection: [
      {
        name: 'bot-https'
        properties: {
          port: backendPort
          protocol: backendProtocol
          cookieBasedAffinity: 'Disabled'
          requestTimeout: 30
          probe: {
            id: resourceId('Microsoft.Network/applicationGateways/probes', appGwName, 'health')
          }
          pickHostNameFromBackendAddress: true
        }
      }
    ]
    httpListeners: [
      {
        name: 'https-listener'
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', appGwName, 'feip')
          }
          frontendPort: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', appGwName, 'fe-443')
          }
          protocol: 'Https'
          sslCertificate: {
            id: resourceId('Microsoft.Network/applicationGateways/sslCertificates', appGwName, 'kvCert')
          }
          requireServerNameIndication: false
        }
      }
      {
        name: 'http-listener'
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', appGwName, 'feip')
          }
          frontendPort: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', appGwName, 'fe-80')
          }
          protocol: 'Http'
        }
      }
    ]
    requestRoutingRules: [
      {
        name: 'https-rule'
        properties: {
          ruleType: 'Basic'
          priority: 100
          httpListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', appGwName, 'https-listener')
          }
          backendAddressPool: {
            id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', appGwName, 'vmss-pool')
          }
          backendHttpSettings: {
            id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', appGwName, 'bot-https')
          }
        }
      }
      {
        name: 'http-redirect-rule'
        properties: {
          ruleType: 'Basic'
          priority: 110
          httpListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', appGwName, 'http-listener')
          }
          redirectConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/redirectConfigurations', appGwName, 'http-to-https')
          }
        }
      }
    ]
    redirectConfigurations: [
      {
        name: 'http-to-https'
        properties: {
          redirectType: 'Permanent'
          targetListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', appGwName, 'https-listener')
          }
          includePath: true
          includeQueryString: true
        }
      }
    ]
    probes: [
      {
        name: 'health'
        properties: {
          protocol: backendProtocol
          path: '/health'
          interval: 30
          timeout: 30
          unhealthyThreshold: 3
          pickHostNameFromBackendHttpSettings: true
          match: {
            statusCodes: [ '200-399' ]
          }
        }
      }
    ]
    enableHttp2: true
  }
}

// Wire VMSS into the AppGw backend pool — output the resource ID; the VMSS
// module's networkInterfaceConfigurations.ipConfigurations[].applicationGatewayBackendAddressPools
// must reference this. (Wired in the orchestrator avatar-stack.bicep.)
output backendPoolId string = '${appGw.id}/backendAddressPools/vmss-pool'
output appGwId string = appGw.id
output appGwName string = appGw.name
output frontendFqdn string = pip.properties.dnsSettings.fqdn
output frontendIp string = pip.properties.ipAddress
