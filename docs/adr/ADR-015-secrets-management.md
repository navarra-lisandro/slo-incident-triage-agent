# ADR-015: Secrets Management Strategy

## Status
Accepted

## Context
The agent requires several secrets to operate:

  ANTHROPIC_API_KEY     Anthropic Claude API access
  LANGSMITH_API_KEY     LangSmith tracing and evaluation
  LANGCHAIN_PROJECT     LangSmith project name (not secret,
                        but environment-specific)
  LANGSMITH_ENDPOINT    LangSmith API endpoint
  LANGSMITH_TRACING     Enable/disable tracing flag

These secrets must be available at runtime across three distinct
execution environments: local development, local Docker container,
and Kubernetes production deployment.

## Decision
Secrets are managed differently per environment:

  Local development     .env file loaded via load_dotenv()
  Docker local          --env-file .env flag or docker-compose
                        env_file directive
  Kubernetes production K8s secrets mounted as environment
                        variables via Helm values.yaml

## Rationale

### Why not command line arguments
Command line arguments were explicitly rejected for secret delivery:
  - Visible in process list via ps aux
  - Visible in shell history
  - Visible in docker inspect output
  - Not appropriate for production secrets under any circumstances

### Why not hardcoded in image
Secrets must never be baked into the Docker image:
  - Images are stored in registries and may be pulled by
    unauthorized parties
  - Image layers are inspectable via docker history
  - Rotating a secret would require rebuilding the image

### Why .env for local development
  - Standard Python pattern — python-dotenv is widely understood
  - .env is in .gitignore — never committed to version control
  - .env.example documents required variables without values
  - load_dotenv() must be called before any LangSmith or
    Anthropic client initialization (see Friction Log)

### Why --env-file for Docker local
  - .env file is not copied into the image (.dockerignore)
  - --env-file injects variables at container runtime
  - docker-compose env_file directive provides the same behavior
    with a single docker compose up command
  - Keeps the image portable and secret-free

### Why K8s secrets for production
  - K8s secrets are base64-encoded and stored in etcd
  - Secrets are mounted as environment variables in the pod
    spec — not visible in image or command line
  - Helm values.yaml references secret key names, not values
  - Secret values are provisioned separately from the chart
  - Managed identity / IRSA (IAM Roles for Service Accounts)
    is the production upgrade path for AWS-managed secrets
    without storing values in K8s at all

## Implementation Per Environment

### Local development
  cp .env.example .env
  # populate with real values
  make run    # load_dotenv() reads .env before graph imports

### Docker local
  docker run --env-file .env -p 8000:8000 slo-incident-triage-agent
  # or
  docker compose up    # docker-compose.yml uses env_file: .env

### Kubernetes production (via Helm)
  # Create the secret before helm install
  kubectl create secret generic slo-incident-triage-agent-secrets \
    --from-literal=ANTHROPIC_API_KEY=<value> \
    --from-literal=LANGSMITH_API_KEY=<value> \
    -n slo-incident-triage-agent

  # Helm chart references the secret in deployment.yaml
  # values.yaml specifies the secret name
  helm install slo-incident-triage-agent ./helm \
    --namespace slo-incident-triage-agent \
    --create-namespace

## Required Secrets Reference

  ANTHROPIC_API_KEY     required   Anthropic API key
  LANGSMITH_API_KEY     required   LangSmith API key
  LANGSMITH_TRACING     required   "true" to enable tracing
  LANGSMITH_ENDPOINT    required   LangSmith endpoint URL
  LANGCHAIN_PROJECT     required   LangSmith project name

## Future Path — Managed Identity
  For production AWS deployments, IRSA (IAM Roles for Service
  Accounts) eliminates the need to store ANTHROPIC_API_KEY in
  K8s secrets. Instead, the pod assumes an IAM role that grants
  access to AWS Secrets Manager, where the key is stored.
  This is the recommended production upgrade path but is out
  of scope for v1.

## Consequences
  - .env must never be committed to version control
  - .env.example must be kept in sync with required variables
  - Docker image contains no secrets — portable across environments
  - K8s secret must be provisioned before helm install
  - Rotating a secret requires updating the K8s secret and
    restarting the pod — no image rebuild required
  - load_dotenv() timing issue documented in Friction Log —
    must be called before agent module imports
