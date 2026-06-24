# syntax=docker/dockerfile:1.6
# Hosted agent for teams-avatar-voice-live (Foundry hosted, Responses protocol).
# Target: linux/amd64, Foundry hosted agents listen on port 8088.
#
# NOTE: build context is the REPO ROOT (so the agentgov governance package and
# the governance policy files can be copied into the image):
#     docker build -f hosted-agent/Dockerfile .
#     az acr build -r <acr> -t lisa:<tag> -f hosted-agent/Dockerfile .

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first (better layer caching)
COPY hosted-agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code. tools/ and personas/ are folders shipped with the image;
# PERSONA_FILE env var can point at any file under personas/.
COPY hosted-agent/main.py hosted-agent/instructions.py hosted-agent/governance_guard.py ./
COPY hosted-agent/tools/    ./tools/
COPY hosted-agent/personas/ ./personas/

# Governance seam: the agentgov runtime package + the policy source of truth it
# reads (agent-registry.yaml + dlp-policy.yaml). Paths mirror the repo layout so
# agentgov's repo-root-relative lookups resolve under /app.
COPY agentgov/ ./agentgov/
COPY governance/agent-registry.yaml ./governance/agent-registry.yaml
COPY governance/security/dlp-policy.yaml ./governance/security/dlp-policy.yaml

EXPOSE 8088

CMD ["python", "main.py"]
