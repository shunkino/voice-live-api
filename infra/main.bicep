// infra/main.bicep
// Deploys all Azure resources required for the Voice Live API sample.
//
// Resources:
//   - Azure AI Services (multi-service) — Voice Live API endpoint
//   - Storage Account               — backing store for AI Hub
//   - Key Vault                     — secret store for AI Hub
//   - Azure AI Hub                  — Foundry workspace hub
//   - Azure AI Project              — Foundry project (PROJECT_ENDPOINT)
//   - GPT realtime model deploy     — model used by Voice Live + agents

@description('Base name used to derive all resource names.')
param baseName string = 'voicelive'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Name of the agent created in the project.')
param agentName string = 'voice-live-scenario'

@description('GPT realtime model to deploy (must be available in the selected region).')
param modelName string = 'gpt-realtime'

@description('Model deployment name referenced by the application.')
param modelDeploymentName string = 'gpt-realtime'

@description('Model version.')
param modelVersion string = '2025-08-28'

@description('Model capacity in thousand-tokens-per-minute (TPM).')
param modelCapacity int = 10

@description('Optional Entra object ID to grant Cognitive Services OpenAI User on the AI Services account. Pass your signed-in user object ID for local keyless auth.')
param openAiUserPrincipalId string = ''

@description('Principal type for openAiUserPrincipalId.')
@allowed([
  'User'
  'Group'
  'ServicePrincipal'
  'ForeignGroup'
  'Device'
])
param openAiUserPrincipalType string = 'User'

// ── Name helpers ────────────────────────────────────────────────────────────
var uniqueSuffix = uniqueString(resourceGroup().id)
var aiServicesName = '${baseName}-ais-${uniqueSuffix}'
// Storage account names: max 24 chars, lowercase alphanumeric only.
// take(...,9) + 'st' (2) + uniqueSuffix (13) = 24 max.
var storageName = '${take(toLower(replace(baseName, '-', '')), 9)}st${uniqueSuffix}'
var kvName = '${baseName}-kv-${take(uniqueSuffix, 6)}'
var hubName = '${baseName}-hub-${uniqueSuffix}'
var projectName = '${baseName}-proj-${uniqueSuffix}'
var cognitiveServicesOpenAIUserRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')

// ── Azure AI Services (Voice Live endpoint) ─────────────────────────────────
resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: aiServicesName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: aiServicesName
    disableLocalAuth: true
  }
}

// Model deployment inside the AI Services account.
// Realtime models are global deployments, so use the GlobalStandard deployment SKU.
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: modelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

// ── Storage Account (required by AI Hub) ────────────────────────────────────
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

// ── Key Vault (required by AI Hub) ──────────────────────────────────────────
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    publicNetworkAccess: 'Enabled'
  }
}

// ── Azure AI Hub ─────────────────────────────────────────────────────────────
resource hub 'Microsoft.MachineLearningServices/workspaces@2024-10-01' = {
  name: hubName
  location: location
  kind: 'Hub'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    storageAccount: storage.id
    keyVault: keyVault.id
    // Connect the AI Services account as a default connection
    hubResourceId: null
  }
}

// AI Services connection inside the Hub
resource hubAiServicesConnection 'Microsoft.MachineLearningServices/workspaces/connections@2024-10-01' = {
  parent: hub
  name: 'ai-services-connection'
  properties: {
    category: 'AIServices'
    target: aiServices.properties.endpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiVersion: '2024-05-01-preview'
      ApiType: 'Azure'
      ResourceId: aiServices.id
      location: aiServices.location
    }
  }
}

// ── Azure AI Project ─────────────────────────────────────────────────────────
resource project 'Microsoft.MachineLearningServices/workspaces@2024-10-01' = {
  name: projectName
  location: location
  kind: 'Project'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hubResourceId: hub.id
  }
}

resource hubOpenAIUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, hub.name, cognitiveServicesOpenAIUserRoleDefinitionId)
  scope: aiServices
  properties: {
    roleDefinitionId: cognitiveServicesOpenAIUserRoleDefinitionId
    principalId: hub.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource projectOpenAIUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, project.name, cognitiveServicesOpenAIUserRoleDefinitionId)
  scope: aiServices
  properties: {
    roleDefinitionId: cognitiveServicesOpenAIUserRoleDefinitionId
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource currentPrincipalOpenAIUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(openAiUserPrincipalId)) {
  name: guid(aiServices.id, openAiUserPrincipalId, cognitiveServicesOpenAIUserRoleDefinitionId)
  scope: aiServices
  properties: {
    roleDefinitionId: cognitiveServicesOpenAIUserRoleDefinitionId
    principalId: openAiUserPrincipalId
    principalType: openAiUserPrincipalType
  }
}

// ── Outputs (used to populate .env) ─────────────────────────────────────────
@description('Voice Live API endpoint (AZURE_VOICE_LIVE_ENDPOINT)')
output voiceLiveEndpoint string = aiServices.properties.endpoint

// API keys are intentionally unavailable because disableLocalAuth is true.
// Run samples with --use-token-credential after RBAC assignment propagation.
output aiServicesName string = aiServices.name

@description('Azure AI Foundry project endpoint (PROJECT_ENDPOINT)')
output projectEndpoint string = 'https://${project.name}.${location}.api.azureml.ms'

@description('Agent name (AGENT_NAME)')
output agentName string = agentName

@description('Model deployment name (MODEL_DEPLOYMENT_NAME)')
output modelDeploymentName string = modelDeploymentName
