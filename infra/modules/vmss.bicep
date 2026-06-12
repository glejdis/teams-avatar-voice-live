// ─────────────────────────────────────────────────────────────────────────────
// VMSS module — Avatar stack
//
// Windows Server 2022 VMSS that hosts:
//   - EchoBot.exe          (Microsoft Graph calling bot, C#)
//   - AvatarSidecar.exe    (Voice Live agent-mode bridge, Python)
//
// Identity:
//   - System-assigned Managed Identity per instance. RBAC granted by the rbac
//     module: AcrPull on ACR, KV Secrets User on KV, Cognitive Services User +
//     Azure AI User on Foundry.
//
// Bootstrap:
//   - Custom Script Extension downloads the latest bot+sidecar zip from the
//     deploy storage container, installs Python + FFmpeg + NSSM, registers
//     both Windows services, and seeds env vars from KV via the AzureKeyVault
//     VM extension (Windows).
//
// Networking:
//   - When useLoadBalancer = true (recommended), VMSS NICs join a Standard LB
//     backend pool + media NAT pool. LB owns the single public IP. Each
//     instance reaches the world via the LB; the LB hairpins the bot's own
//     8445 TCP self-test (per-instance Public IPs do NOT support hairpin and
//     break the Skype Bots Media SDK self-test).
//   - When useLoadBalancer = false, each instance gets its own per-instance
//     public IP (legacy mode — the bot media SDK self-test will fail in this
//     mode; only useful for diagnostics).
//
// Upgrades:
//   - upgradePolicy mode = Rolling. Health probe = HTTP /health on port 5001.
//   - automaticRepair = enabled.
// ─────────────────────────────────────────────────────────────────────────────

@description('Region.')
param location string

@description('VMSS name.')
@maxLength(15)
param vmssName string

@description('Instance count. 1 = no concurrency, 2 = hot-standby HA.')
@minValue(1)
@maxValue(10)
param instanceCount int = 1

@description('VM SKU. Avatar rendering is CPU-bound; D4s_v5 minimum.')
param vmSize string = 'Standard_D4s_v5'

@description('Local admin username (for Bastion-RDP break-glass).')
param adminUsername string = 'azureuser'

@secure()
@description('Local admin password.')
param adminPassword string

@description('Subnet ID (vmss subnet from the network module).')
param subnetId string

@description('Use Standard Load Balancer for inbound traffic (recommended). When false, each instance gets its own per-instance public IP — useful for diagnostics only.')
param useLoadBalancer bool = true

@description('Standard LB backend pool ID. Required when useLoadBalancer = true.')
param lbBackendPoolId string = ''

@description('Standard LB inbound NAT pool ID for media (8445 per-instance). Required when useLoadBalancer = true.')
param lbMediaNatPoolId string = ''

@description('DNS label prefix for the per-instance public IP (legacy mode only). Becomes <prefix>-<n>.<region>.cloudapp.azure.com.')
param dnsLabelPrefix string

@description('Bootstrap blob URL (zip with bot binaries + sidecar). MI must be authorised on this account.')
param bootstrapZipUrl string = ''

@description('URL to install.ps1 (separate blob from the bot zip). MI must be authorised. Required when bootstrapZipUrl is set.')
param installScriptUrl string = ''

@description('Custom Script Extension command line. Receives bootstrapZipUrl as %1. Filename must match the last URL segment of installScriptUrl (CSE saves downloads using the URL leaf name).')
param bootstrapCommand string = 'powershell.exe -ExecutionPolicy Unrestricted -File .\\install-latest.ps1'

@description('Key Vault name where bot-aad-secret etc. live (mounted via AKV VM extension).')
param keyVaultName string

@description('PFX-as-secret name in KV for the bot TLS cert (used when AppGw is disabled).')
param tlsCertSecretName string = 'bot-tls-cert'

@description('Install the AzureKeyVault VM extension to sync the cert into LocalMachine\\My.')
param installCertFromKeyVault bool = true

@description('Log Analytics workspace ID for diag settings + monitoring agent.')
param logAnalyticsWorkspaceId string = ''

@description('App Insights connection string (passed through to the apps as env var).')
param appInsightsConnectionString string = ''

@description('Public service FQDN that Microsoft Graph will hit for signaling and that the bot media SDK uses for its self-test. Typically the LB FQDN. Empty string disables the override.')
param serviceFqdn string = ''

@description('First port in the LB media NAT pool. Bot reads its instance index and uses (mediaNatStartPort + index) as InstancePublicPort.')
param mediaNatStartPort int = 8445

@description('Voice Live / Foundry endpoint used by avatar-sidecar.')
param voiceLiveEndpoint string = ''

@description('Hosted Foundry agent name used by avatar-sidecar.')
param agentName string = ''

@description('Hosted Foundry agent version used by avatar-sidecar. Empty means latest/routed version.')
param agentVersion string = ''

@description('Foundry project name used by avatar-sidecar.')
param foundryProjectName string = ''

@description('Tenant ID used by sidecar token pinning.')
param azureTenantId string = ''

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

@description('Bump this string to force CSE bootstrap to re-run on existing instances (e.g., after re-uploading install.ps1 or lisa-latest.zip with the same blob URL).')
param cseRevision string = '1'

@description('Resource tags.')
param tags object = {}

var nicName = '${vmssName}-nic'
var ipConfigName = '${vmssName}-ipcfg'

resource vmss 'Microsoft.Compute/virtualMachineScaleSets@2024-07-01' = {
  name: vmssName
  location: location
  tags: tags
  sku: {
    name: vmSize
    tier: 'Standard'
    capacity: instanceCount
  }
  identity: { type: 'SystemAssigned' }
  properties: {
    overprovision: false
    singlePlacementGroup: false
    upgradePolicy: {
      // Manual upgrade mode: requires explicit instance reimage to roll. Avoids
      // the ARM requirement of a health probe / ApplicationHealth extension that
      // Rolling mode imposes. Once a TCP/HTTP health probe is wired (port 8445
      // signaling endpoint or a dedicated /health on the bot), switch back to
      // 'Rolling' and re-enable automaticRepairsPolicy below.
      mode: 'Manual'
      automaticOSUpgradePolicy: {
        enableAutomaticOSUpgrade: false
      }
    }
    // automaticRepairsPolicy disabled: also requires a health signal. Re-enable
    // alongside the ApplicationHealth extension in a follow-up.
    automaticRepairsPolicy: {
      enabled: false
    }
    virtualMachineProfile: {
      osProfile: {
        computerNamePrefix: take(vmssName, 9)
        adminUsername: adminUsername
        adminPassword: adminPassword
        windowsConfiguration: {
          enableAutomaticUpdates: true
          provisionVMAgent: true
        }
        secrets: []
      }
      diagnosticsProfile: {
        bootDiagnostics: {
          enabled: true
        }
      }
      storageProfile: {
        imageReference: {
          publisher: 'MicrosoftWindowsServer'
          offer: 'WindowsServer'
          sku: '2022-datacenter-azure-edition'
          version: 'latest'
        }
        osDisk: {
          createOption: 'FromImage'
          managedDisk: { storageAccountType: 'Premium_LRS' }
          caching: 'ReadWrite'
        }
      }
      networkProfile: {
        networkInterfaceConfigurations: [
          {
            name: nicName
            properties: {
              primary: true
              ipConfigurations: [
                {
                  name: ipConfigName
                  properties: union(
                    {
                      subnet: { id: subnetId }
                    },
                    useLoadBalancer ? {
                      loadBalancerBackendAddressPools: [
                        { id: lbBackendPoolId }
                      ]
                      loadBalancerInboundNatPools: [
                        { id: lbMediaNatPoolId }
                      ]
                    } : {
                      publicIPAddressConfiguration: {
                        name: '${vmssName}-pip'
                        properties: {
                          idleTimeoutInMinutes: 15
                          dnsSettings: {
                            domainNameLabel: dnsLabelPrefix
                          }
                        }
                        sku: { name: 'Standard' }
                      }
                    }
                  )
                }
              ]
            }
          }
        ]
      }
      extensionProfile: {
        extensions: concat(
          // Custom Script Extension bootstrap. fileUris MUST point at the
          // standalone install.ps1 blob (not the zip). install.ps1 then uses
          // the VMSS MI to fetch the bot zip from `bootstrapZipUrl`.
          // managedIdentity: {} = auth as the VMSS system-assigned MI when
          // downloading the file from storage (required when the storage
          // account disallows anonymous public access).
          (empty(bootstrapZipUrl) || empty(installScriptUrl)) ? [] : [
            {
              name: 'avatar-bootstrap'
              properties: {
                publisher: 'Microsoft.Compute'
                type: 'CustomScriptExtension'
                typeHandlerVersion: '1.10'
                autoUpgradeMinorVersion: true
                // forceUpdateTag derives from inputs that affect commandToExecute so
                // CSE rerun is triggered whenever the command line or script URL changes.
                // (protectedSettings are write-only, so Bicep can't detect their drift.)
                forceUpdateTag: uniqueString(bootstrapCommand, installScriptUrl, bootstrapZipUrl, serviceFqdn, string(mediaNatStartPort), voiceLiveEndpoint, agentName, agentVersion, foundryProjectName, azureTenantId, voiceLiveApiVersion, voiceLiveVoice, avatarCharacter, avatarStyle, avatarBackgroundImageUrl, avatarBackgroundColor, agentLanguage, cseRevision)
                settings: {
                  fileUris: [ installScriptUrl ]
                }
                protectedSettings: {
                  managedIdentity: {}
                  commandToExecute: '${bootstrapCommand} ${bootstrapZipUrl} ${keyVaultName} ${tlsCertSecretName} "${appInsightsConnectionString}" "${serviceFqdn}" ${mediaNatStartPort} "${voiceLiveEndpoint}" "${agentName}" "${agentVersion}" "${foundryProjectName}" "${azureTenantId}" "${voiceLiveApiVersion}" "${voiceLiveVoice}" "${avatarCharacter}" "${avatarStyle}" "${agentLanguage}" "${avatarBackgroundImageUrl}" "${avatarBackgroundColor}"'
                }
              }
            }
          ],
          // KV-cert sync: pulls bot-tls-cert from KV into LocalMachine\My on each instance.
          installCertFromKeyVault ? [
            {
              name: 'KVVMExtensionForWindows'
              properties: {
                publisher: 'Microsoft.Azure.KeyVault'
                type: 'KeyVaultForWindows'
                typeHandlerVersion: '3.0'
                autoUpgradeMinorVersion: true
                settings: {
                  secretsManagementSettings: {
                    pollingIntervalInS: '3600'
                    certificateStoreName: 'MY'
                    linkOnRenewal: false
                    certificateStoreLocation: 'LocalMachine'
                    requireInitialSync: true
                    observedCertificates: [
                      'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${tlsCertSecretName}'
                    ]
                  }
                  authenticationSettings: {
                    msiEndpoint: 'http://169.254.169.254/metadata/identity/oauth2/token'
                  }
                }
              }
            }
          ] : [],
          // Log Analytics agent (Azure Monitor Agent).
          empty(logAnalyticsWorkspaceId) ? [] : [
            {
              name: 'AzureMonitorWindowsAgent'
              properties: {
                publisher: 'Microsoft.Azure.Monitor'
                type: 'AzureMonitorWindowsAgent'
                typeHandlerVersion: '1.0'
                autoUpgradeMinorVersion: true
                enableAutomaticUpgrade: true
                settings: {}
              }
            }
          ]
        )
      }
    }
  }
}

output vmssId string = vmss.id
output vmssName string = vmss.name
output vmssPrincipalId string = vmss.identity.principalId
