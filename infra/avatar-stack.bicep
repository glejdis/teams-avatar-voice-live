// ─────────────────────────────────────────────────────────────────────────────
// Avatar stack — top-level orchestrator (modular, env-parameterised)
//
// Composes:
//   - network   : VNet + subnets (vmss, appgw, AzureBastionSubnet, privateLink)
//                 + NSGs (no operator-IP rules — Bastion replaces them)
//   - keyvault  : RBAC-mode KV (bot AAD secret + bot TLS PFX)
//   - kvprivatelink : Key Vault private endpoint + private DNS zone/link
//   - monitor   : Log Analytics + App Insights + alert action group
//   - bastion   : Azure Bastion (operator access; Basic SKU)
//   - vmss      : Win Server 2022 VMSS (bot + sidecar)
//                 + Custom Script Extension bootstrap
//                 + AzureKeyVault VM extension (auto-syncs cert from KV)
//                 + Azure Monitor Agent
//   - appgw     : (optional) App Gateway WAF_v2 fronting the signaling port,
//                 KV-managed cert, auto-rotates
//   - rbac      : VMSS MI → AcrPull, KV Secrets User, Cognitive Services User,
//                 Azure AI User, Storage Blob Data Reader (deploy artifacts)
//
// Inputs reference resources defined in `infra/main.bicep` (Foundry account,
// ACR if present) by NAME, so this module is environment-portable.
// ─────────────────────────────────────────────────────────────────────────────

targetScope = 'resourceGroup'

@description('Region.')
param location string = resourceGroup().location

@description('Common name prefix (lowercase, <=13 chars). E.g. "<NAME_PREFIX>".')
@maxLength(13)
param namePrefix string = '<NAME_PREFIX>'

@description('Resource tags applied to every resource.')
param tags object = {
  workload: 'avatar'
  managedBy: 'bicep'
}

@description('Existing Foundry (Cognitive Services) account name. From main.bicep output.')
param foundryAccountName string

@description('Existing Foundry project name used by Voice Live agent mode.')
param foundryProjectName string

@description('Hosted Foundry agent name used by the avatar sidecar. Must match the agent name created by hosted-agent/deploy.sh (default there: lisa).')
param agentName string = 'lisa'

@description('Hosted Foundry agent version. Empty means latest/routed version.')
param agentVersion string = ''

@description('Tenant ID used by sidecar token pinning.')
param azureTenantId string = tenant().tenantId

@description('Voice Live API version for the sidecar.')
param voiceLiveApiVersion string = '2026-01-01-preview'

@description('Azure voice name used by the sidecar when no agent metadata voice config is attached.')
param voiceLiveVoice string = 'en-US-AvaMultilingualNeural'

@description('Avatar character used by the sidecar.')
param avatarCharacter string = 'lisa'

@description('Avatar style used by the sidecar.')
param avatarStyle string = 'casual-sitting'

@description('Optional public HTTPS URL the Voice Live service can fetch to use as the avatar background image. Empty = use avatarBackgroundColor instead.')
param avatarBackgroundImageUrl string = ''

@description('Optional CSS hex color (e.g. "#00ff00" or "#FFFFFFFF") used as the avatar background when no image URL is set. Empty = Voice Live default (transparent).')
param avatarBackgroundColor string = ''

@description('Language hint for sidecar transcription/VAD config.')
param agentLanguage string = 'en'

@description('Existing ACR name. Empty = skip ACR-related RBAC.')
param acrName string = ''

@description('Storage account name used for VMSS bootstrap artifacts (zip + install.ps1).')
param deployStorageAccountName string = ''

@description('Bootstrap blob URL (signed or readable by VMSS MI). Required to install the bot+sidecar.')
param bootstrapZipUrl string = ''

@description('URL to install.ps1 (separate blob). Required when bootstrapZipUrl is set.')
param installScriptUrl string = ''

@description('Bump this string to force CSE bootstrap to re-run on existing instances when install.ps1 / lisa-latest.zip changed but blob URLs are stable.')
param cseRevision string = '1'

@description('Install the AzureKeyVault VM extension to sync bot-tls-cert into LocalMachine\\My. Set false on first deploy (before the cert exists) and re-deploy with true after.')
param installCertFromKeyVault bool = true

@description('VMSS instance count. 1 = no concurrency. 2 = hot-standby HA.')
@minValue(1)
@maxValue(10)
param vmssInstanceCount int = 1

@description('VM SKU.')
param vmSize string = 'Standard_D4s_v5'

@description('Local admin user (break-glass via Bastion).')
param adminUsername string = 'azureuser'

@secure()
@description('Local admin password.')
param adminPassword string

@description('DNS label prefix for VMSS instance public IPs (used only when useLoadBalancer = false; legacy mode).')
param vmssDnsLabelPrefix string = '${namePrefix}-i'

@description('Use Standard Load Balancer in front of the VMSS (recommended). Required for the Microsoft Graph calling bot media SDK self-test to pass (per-instance Public IPs do NOT support NAT hairpin and the SDK can\'t be fooled with /etc/hosts because it discovers its public IP via IMDS).')
param useLoadBalancer bool = true

@description('DNS label prefix for the LB public IP. Empty = no DNS label.')
param lbDnsLabelPrefix string = '${namePrefix}-lb'

@description('Email address for monitor alerts. Empty = no alerts.')
param alertEmail string = ''

@allowed([ 'Enabled', 'Disabled' ])
@description('Key Vault public network access. Production should use Disabled with a private endpoint.')
param keyVaultPublicNetworkAccess string = 'Enabled'

@allowed([ 'Allow', 'Deny' ])
@description('Key Vault network ACL default action. Production should use Deny.')
param keyVaultNetworkAclsDefaultAction string = 'Allow'

// ── Toggles ──
@description('Provision Application Gateway in front of the signaling port. Requires kvCertSecretId.')
param enableAppGateway bool = false

@description('Versionless KV secret URI for the AppGw TLS PFX. Required when enableAppGateway = true.')
param appGwKvCertId string = ''

@description('User-assigned MI ID with KV Secrets User on the AppGw cert vault. Required when enableAppGateway = true.')
param appGwIdentityId string = ''

@description('DNS label for the AppGw frontend public IP.')
param appGwDnsLabelPrefix string = '${namePrefix}-app'

@description('Provision a Key Vault private endpoint in the privateLink subnet and link privatelink.vaultcore.azure.net to the Avatar VNet.')
param enableKeyVaultPrivateEndpoint bool = false

@description('Private DNS zone name for Key Vault private endpoints.')
param keyVaultPrivateDnsZoneName string = 'privatelink.vaultcore.azure.net'

// ── Network sizing (overridable per env) ──
param vnetAddressSpace string = '10.42.0.0/16'
param vmssSubnetPrefix string = '10.42.0.0/24'
param appGwSubnetPrefix string = '10.42.1.0/27'
param bastionSubnetPrefix string = '10.42.2.0/26'
param privateLinkSubnetPrefix string = '10.42.3.0/27'

// ─────────────────────────────────────────────────────────────────────────────

module network 'modules/network.bicep' = {
  name: 'avatar-network'
  params: {
    location: location
    namePrefix: namePrefix
    vnetAddressSpace: vnetAddressSpace
    vmssSubnetPrefix: vmssSubnetPrefix
    appGwSubnetPrefix: appGwSubnetPrefix
    bastionSubnetPrefix: bastionSubnetPrefix
    privateLinkSubnetPrefix: privateLinkSubnetPrefix
    enableLoadBalancer: useLoadBalancer
    lbDnsLabelPrefix: lbDnsLabelPrefix
    tags: tags
  }
}

module monitor 'modules/monitor.bicep' = {
  name: 'avatar-monitor'
  params: {
    location: location
    namePrefix: namePrefix
    alertEmail: alertEmail
    tags: tags
  }
}

module kv 'modules/keyvault.bicep' = {
  name: 'avatar-keyvault'
  params: {
    location: location
    keyVaultName: '${replace(namePrefix, '-', '')}kv'
    publicNetworkAccess: keyVaultPublicNetworkAccess
    networkAclsDefaultAction: keyVaultNetworkAclsDefaultAction
    tags: tags
  }
}

module kvPrivateEndpoint 'modules/keyvault-private-endpoint.bicep' = if (enableKeyVaultPrivateEndpoint) {
  name: 'avatar-kv-private-endpoint'
  params: {
    location: location
    namePrefix: namePrefix
    keyVaultId: kv.outputs.keyVaultId
    keyVaultName: kv.outputs.keyVaultName
    privateEndpointSubnetId: network.outputs.privateLinkSubnetId
    vnetId: network.outputs.vnetId
    privateDnsZoneName: keyVaultPrivateDnsZoneName
    tags: tags
  }
}

module bastion 'modules/bastion.bicep' = {
  name: 'avatar-bastion'
  params: {
    location: location
    namePrefix: namePrefix
    bastionSubnetId: network.outputs.bastionSubnetId
    tags: tags
  }
}

module vmss 'modules/vmss.bicep' = {
  name: 'avatar-vmss'
  params: {
    location: location
    vmssName: take(replace(namePrefix, '-', ''), 12)
    instanceCount: vmssInstanceCount
    vmSize: vmSize
    adminUsername: adminUsername
    adminPassword: adminPassword
    subnetId: network.outputs.vmssSubnetId
    useLoadBalancer: useLoadBalancer
    lbBackendPoolId: network.outputs.lbBackendPoolId
    lbMediaNatPoolId: network.outputs.lbMediaNatPoolId
    dnsLabelPrefix: vmssDnsLabelPrefix
    bootstrapZipUrl: bootstrapZipUrl
    installScriptUrl: installScriptUrl
    installCertFromKeyVault: installCertFromKeyVault
    keyVaultName: kv.outputs.keyVaultName
    logAnalyticsWorkspaceId: monitor.outputs.workspaceId
    appInsightsConnectionString: monitor.outputs.appInsightsConnectionString
    serviceFqdn: useLoadBalancer ? network.outputs.lbFqdn : ''
    mediaNatStartPort: network.outputs.mediaNatStartPort
    voiceLiveEndpoint: 'https://${foundryAccountName}.services.ai.azure.com/'
    agentName: agentName
    agentVersion: agentVersion
    foundryProjectName: foundryProjectName
    azureTenantId: azureTenantId
    voiceLiveApiVersion: voiceLiveApiVersion
    voiceLiveVoice: voiceLiveVoice
    avatarCharacter: avatarCharacter
    avatarStyle: avatarStyle
    avatarBackgroundImageUrl: avatarBackgroundImageUrl
    avatarBackgroundColor: avatarBackgroundColor
    agentLanguage: agentLanguage
    cseRevision: cseRevision
    tags: tags
  }
}

module rbac 'modules/rbac.bicep' = {
  name: 'avatar-rbac'
  params: {
    vmssPrincipalId: vmss.outputs.vmssPrincipalId
    keyVaultName: kv.outputs.keyVaultName
    foundryAccountName: foundryAccountName
    acrName: acrName
    deployStorageAccountName: deployStorageAccountName
  }
}

module appgw 'modules/appgw.bicep' = if (enableAppGateway) {
  name: 'avatar-appgw'
  params: {
    location: location
    namePrefix: namePrefix
    subnetId: network.outputs.appGwSubnetId
    userAssignedIdentityId: appGwIdentityId
    kvCertId: appGwKvCertId
    dnsLabelPrefix: appGwDnsLabelPrefix
    tags: tags
  }
}

// ── Diagnostic settings (platform logs → Log Analytics) ──
module diagnostics 'modules/diagnostics.bicep' = {
  name: 'avatar-diagnostics'
  params: {
    workspaceId: monitor.outputs.workspaceId
    keyVaultName: kv.outputs.keyVaultName
    vmssName: vmss.outputs.vmssName
    appGwName: enableAppGateway ? appgw!.outputs.appGwName : ''
  }
}

// ── Cross-module alerts (need both vmss & monitor outputs) ──
resource alertVmssUnhealthy 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(alertEmail)) {
  name: '${namePrefix}-vmss-unhealthy'
  location: 'global'
  tags: tags
  properties: {
    description: 'VMSS has unhealthy instances for >5 min — auto-repair may be running.'
    severity: 2
    enabled: true
    scopes: [ resourceId('Microsoft.Compute/virtualMachineScaleSets', vmss.outputs.vmssName) ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Compute/virtualMachineScaleSets'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'unhealthyVMs'
          metricNamespace: 'Microsoft.Compute/virtualMachineScaleSets'
          metricName: 'VmAvailabilityMetric'
          operator: 'LessThan'
          threshold: 1
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: monitor.outputs.actionGroupId
      }
    ]
  }
}

// ── Outputs ──
output vmssName string = vmss.outputs.vmssName
output keyVaultName string = kv.outputs.keyVaultName
output keyVaultUri string = kv.outputs.keyVaultUri
output keyVaultPrivateEndpointId string = enableKeyVaultPrivateEndpoint ? kvPrivateEndpoint!.outputs.privateEndpointId : ''
output keyVaultPrivateDnsZoneId string = enableKeyVaultPrivateEndpoint ? kvPrivateEndpoint!.outputs.privateDnsZoneId : ''
output bastionName string = bastion.outputs.bastionName
output workspaceId string = monitor.outputs.workspaceId
output appInsightsConnectionString string = monitor.outputs.appInsightsConnectionString
output appGwFqdn string = enableAppGateway ? appgw!.outputs.frontendFqdn : ''
output lbFqdn string = useLoadBalancer ? network.outputs.lbFqdn : ''
output lbPublicIp string = useLoadBalancer ? network.outputs.lbPublicIp : ''
output mediaNatStartPort int = network.outputs.mediaNatStartPort
