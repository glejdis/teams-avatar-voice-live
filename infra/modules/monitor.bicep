// ─────────────────────────────────────────────────────────────────────────────
// Monitor module — Avatar stack
//
// Log Analytics + App Insights + optional email action group. Metric alerts
// that need other modules' resource IDs are wired in the orchestrator
// (avatar-stack.bicep) to avoid module-output cycles.
// ─────────────────────────────────────────────────────────────────────────────

@description('Region.')
param location string

@description('Common name prefix.')
param namePrefix string

@description('Log Analytics workspace retention (days).')
@minValue(30)
@maxValue(730)
param logRetentionDays int = 90

@description('Email address for alert notifications. Empty disables alerts.')
param alertEmail string = ''

@description('Resource tags.')
param tags object = {}

var workspaceName = '${namePrefix}-law'
var appInsightsName = '${namePrefix}-appi'
var actionGroupName = '${namePrefix}-ag'

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: logRetentionDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2024-10-01-preview' = if (!empty(alertEmail)) {
  name: actionGroupName
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take(namePrefix, 12)
    enabled: true
    emailReceivers: [
      {
        name: 'opsEmail'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

output workspaceId string = law.id
output workspaceName string = law.name
output appInsightsId string = appi.id
output appInsightsConnectionString string = appi.properties.ConnectionString
output appInsightsInstrumentationKey string = appi.properties.InstrumentationKey
output actionGroupId string = empty(alertEmail) ? '' : actionGroup!.id
