targetScope = 'resourceGroup'

@description('Existing VMSS name.')
param vmssName string = '<VMSS_NAME>'

@description('Bootstrap extension instance name.')
param bootstrapExtensionName string = 'avatar-bootstrap'

@description('Bootstrap blob URL for the bot + sidecar artifact zip. VMSS managed identity must have Storage Blob Data Reader.')
param bootstrapZipUrl string

@description('URL to install.ps1 as a standalone blob. VMSS managed identity must have Storage Blob Data Reader.')
param installScriptUrl string

@description('Custom Script Extension command prefix. Filename must match the last URL segment of installScriptUrl.')
param bootstrapCommand string = 'powershell.exe -ExecutionPolicy Unrestricted -File .\\install-latest.ps1'

@description('Key Vault name where bot secrets and TLS certificate live.')
param keyVaultName string = '<KEY_VAULT_NAME>'

@description('PFX-as-secret name in Key Vault for the bot TLS certificate.')
param tlsCertSecretName string = 'bot-tls-cert'

@description('Application Insights connection string for bot and sidecar telemetry.')
param appInsightsConnectionString string

@description('Public service FQDN exposed by the load balancer.')
param serviceFqdn string = '<BOT_FQDN>'

@description('First port of the load balancer media NAT pool.')
param mediaNatStartPort int = 8445

@description('Existing Foundry account name used to derive the Voice Live endpoint.')
param foundryAccountName string = '<FOUNDRY_ACCOUNT_NAME>'

@description('Foundry project name used by avatar-sidecar.')
param foundryProjectName string = '<FOUNDRY_PROJECT_NAME>'

@description('Hosted Foundry agent name used by avatar-sidecar. Must match the agent name created by hosted-agent/deploy.sh (default there: lisa).')
param agentName string = 'lisa'

@description('Hosted Foundry agent version used by avatar-sidecar. Empty means latest/routed version.')
param agentVersion string = '5'

@description('Tenant ID used by sidecar token pinning.')
param azureTenantId string = tenant().tenantId

@description('Voice Live API version used by avatar-sidecar.')
param voiceLiveApiVersion string = '2026-01-01-preview'

@description('Azure voice name used by avatar-sidecar.')
param voiceLiveVoice string = 'en-US-AvaMultilingualNeural'

@description('Avatar character used by avatar-sidecar.')
param avatarCharacter string = 'lisa'

@description('Avatar style used by avatar-sidecar.')
param avatarStyle string = 'casual-sitting'

@description('Optional public HTTPS URL the Voice Live service can fetch to use as the avatar background image. Empty = use avatarBackgroundColor instead.')
param avatarBackgroundImageUrl string = ''

@description('Optional CSS hex color (e.g. "#00ff00" or "#FFFFFFFF") used as the avatar background when no image URL is set. Empty = Voice Live default (transparent).')
param avatarBackgroundColor string = ''

@description('Language hint used by avatar-sidecar.')
param agentLanguage string = 'en'

@description('Bump to force the CSE model to rerun when durable blob URLs stay unchanged but artifact content changes.')
param cseRevision string = '1'

@description('Storage account name for per-call cost telemetry (callcosts table). Empty disables cost persistence on the sidecar. The VMSS managed identity must have Storage Table Data Contributor on this account.')
param costStoreAccount string = ''

@description('Azure Table name for per-call cost records.')
param costStoreTable string = 'callcosts'

resource vmss 'Microsoft.Compute/virtualMachineScaleSets@2024-11-01' existing = {
  name: vmssName
}

var voiceLiveEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/'

resource avatarBootstrap 'Microsoft.Compute/virtualMachineScaleSets/extensions@2024-11-01' = {
  name: bootstrapExtensionName
  parent: vmss
  properties: {
    publisher: 'Microsoft.Compute'
    type: 'CustomScriptExtension'
    typeHandlerVersion: '1.10'
    autoUpgradeMinorVersion: true
    forceUpdateTag: uniqueString(bootstrapCommand, installScriptUrl, bootstrapZipUrl, serviceFqdn, string(mediaNatStartPort), voiceLiveEndpoint, agentName, agentVersion, foundryProjectName, azureTenantId, voiceLiveApiVersion, voiceLiveVoice, avatarCharacter, avatarStyle, avatarBackgroundImageUrl, avatarBackgroundColor, agentLanguage, costStoreAccount, costStoreTable, cseRevision)
    settings: {
      fileUris: [
        installScriptUrl
      ]
    }
    protectedSettings: {
      managedIdentity: {}
      commandToExecute: '${bootstrapCommand} ${bootstrapZipUrl} ${keyVaultName} ${tlsCertSecretName} "${appInsightsConnectionString}" "${serviceFqdn}" ${mediaNatStartPort} "${voiceLiveEndpoint}" "${agentName}" "${agentVersion}" "${foundryProjectName}" "${azureTenantId}" "${voiceLiveApiVersion}" "${voiceLiveVoice}" "${avatarCharacter}" "${avatarStyle}" "${agentLanguage}" "${avatarBackgroundImageUrl}" "${avatarBackgroundColor}" "${costStoreAccount}" "${costStoreTable}"'
    }
  }
}

output extensionName string = avatarBootstrap.name
output extensionResourceId string = avatarBootstrap.id
