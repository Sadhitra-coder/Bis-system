param location string = resourceGroup().location
param environmentName string = 'complywise-env'
param containerAppName string = 'bis-system-v5'
param containerImage string
@secure()
param internalServiceKey string
param registryServer string = ''
param registryUsername string = ''
@secure()
param registryPassword string = ''

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8001
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: empty(registryPassword) ? [
        {
          name: 'internal-service-key'
          value: internalServiceKey
        }
      ] : [
        {
          name: 'internal-service-key'
          value: internalServiceKey
        }
        {
          name: 'registry-password'
          value: registryPassword
        }
      ]
      registries: empty(registryPassword) ? [] : [
        {
          server: registryServer
          username: registryUsername
          passwordSecretRef: 'registry-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'bis-system'
          image: containerImage
          resources: {
            cpu: json('2.0')
            memory: '4.0Gi'
          }
          env: [
            {
              name: 'PORT'
              value: '8001'
            }
            {
              name: 'HOST'
              value: '0.0.0.0'
            }
            {
              name: 'LLM_ENABLED'
              value: 'false'
            }
            {
              name: 'INTERNAL_SERVICE_KEY'
              secretRef: 'internal-service-key'
            }
            {
              name: 'TRANSFORMERS_NO_TF'
              value: '1'
            }
            {
              name: 'USE_TF'
              value: '0'
            }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/health'
                port: 8001
              }
              initialDelaySeconds: 20
              periodSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8001
              }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8001
              }
              periodSeconds: 15
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output fqdn string = containerApp.properties.configuration.ingress.fqdn
