// infra/main.bicepparam
// Default parameter values for the Voice Live API deployment.
// Override any value here or pass --parameters on the CLI.

using 'main.bicep'

// Friendly prefix for all resource names
param baseName = 'voicelive'

// Target Azure region — GPT realtime models are available as global deployments
// in supported resource regions such as eastus2 and swedencentral.
param location = 'eastus2'

// Agent name that the application creates / connects to
param agentName = 'voice-live-scenario'

// GPT Realtime model settings
param modelName = 'gpt-realtime'
param modelDeploymentName = 'gpt-realtime'
param modelVersion = '2025-08-28'
param modelCapacity = 10
