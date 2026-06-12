# `hosted-agent/` — Foundry-hosted agent

The persona "brain" that runs as a Foundry-hosted container. Talks to its
caller (the VMSS sidecar in production, or the `browser-fallback/` FastAPI
page in local-dev) over the OpenAI **Responses** protocol on port `8088`.
The caller wraps the responses into an Azure Voice Live session and pipes
the audio + avatar video into the Teams meeting.

This directory builds **one** Docker image. The persona (Markdown) and the
Voice Live runtime config (JSON) are external to the image, so swapping
the persona or tweaking voice/VAD does not require a code change.

## Layout

| File | Role |
|---|---|
| `main.py` | Agent Framework `Agent` factory. Loads the persona via `instructions.py`, registers the example `lookup_job_requirements` tool, serves the Responses protocol on `0.0.0.0:8088`. |
| `instructions.py` | Persona loader. Reads the Markdown file pointed to by `PERSONA_FILE` (default: `personas/lisa.md`) and exposes it as `INSTRUCTIONS`. |
| `personas/` | Editable Markdown personas. `lisa.md` is the shipped HR example; `generic.md` is a minimal non-domain-specific starter. See [`personas/README.md`](personas/README.md). |
| `tools/job_requirements.py` | Example knowledge-base tool used by the Lisa persona. Replace or remove when you ship a different persona. |
| `voice-live-config.json` | Voice Live runtime config (voice, VAD, transcription, avatar character/style/background). Attached to the agent definition as the `microsoft.voice-live.configuration` metadata block — **required** for Voice Live agent mode. |
| `Dockerfile` | `python:3.12-slim`, `EXPOSE 8088`. |
| `deploy.sh` | Build + push to ACR, create a new agent version, then PATCH the Voice Live metadata onto the version. See the env-var block at the top of the file for required inputs. |
| `.env.example` | Local-dev template. **Not needed inside the container** — Foundry injects env vars at runtime via the agent definition. |

## Deploy (CI)

The CI workflow [`.github/workflows/agent-deploy.yml`](../.github/workflows/agent-deploy.yml)
runs `deploy.sh` for you on pushes that touch `hosted-agent/**`. It needs
the env vars / variables documented in [`docs/DEPLOY.md`](../docs/DEPLOY.md).

## Deploy (manual one-off)

```bash
cd hosted-agent
export ACR_NAME=<your-acr>             FOUNDRY_RG=<rg-of-foundry-resource>
export FOUNDRY_NAME=<foundry-resource> FOUNDRY_PROJECT=<project-name>
export AZURE_TENANT_ID=<tenant-id>     AZURE_SUBSCRIPTION=<subscription-id>
./deploy.sh
```

The script prints the new agent version. Wire it into the caller via
`AGENT_FOUNDRY_AGENT_VERSION=<version>` (in `browser-fallback/.env` for
the browser path, or in the VMSS sidecar env vars for production).

## Local dev

```bash
cd hosted-agent
python -m venv .venv && source .venv/bin/activate   # or .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set AGENT_FOUNDRY_PROJECT_ENDPOINT at minimum.
python main.py                                      # listens on http://localhost:8088/responses
```

Smoke-test the running agent like any OpenAI Responses API server:

```bash
curl -X POST http://localhost:8088/responses \
  -H "Content-Type: application/json" \
  -d '{"model": "default", "input": "Hi, who are you?"}'
```

## Swapping the persona

1. Drop a new Markdown file under `personas/` (e.g. `personas/support_agent.md`).
2. Set `PERSONA_FILE=personas/<your-file>.md` in `.env` (or in the container
   environment / Foundry agent env block).
3. Restart the agent.

The shipped `personas/lisa.md` is HR-specific and relies on the
`lookup_job_requirements` tool wired up in `main.py`. A truly different
persona (e.g. customer support) will probably want to **remove** that tool
from `main.py` and register its own. See
[`personas/README.md`](personas/README.md) for authoring guidance,
voice-specific style rules, and the anti-patterns to avoid.

## Gotchas

- `azure-ai-agentserver-agentframework` is pinned to `1.0.0b16` — `b17`
  broke the `agent_framework.azure` imports.
- The Foundry **agent management** API lives on
  `<resource>.services.ai.azure.com`, **not**
  `<resource>.cognitiveservices.azure.com`.
- Always create a **new version with a new tag** — re-pushing the same tag
  does NOT update a running version because Foundry pins by digest at
  version-create time. `deploy.sh` defaults `IMAGE_TAG` to a timestamp.
- Capability host (`accountcaphost`, `capabilityHostKind=Agents`,
  `enablePublicHostingEnvironment=true`) must already be provisioned on the
  Foundry account — it is **not** auto-created.
