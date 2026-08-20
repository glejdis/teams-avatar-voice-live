# Architecture

`teams_avatar_voice_live` ships an AI agent that joins a Microsoft Teams meeting
as a video avatar, driven by **Azure Voice Live** (low-latency speech-to-speech
on top of Azure OpenAI / Foundry). The agent personality lives in an editable
Markdown file under [`hosted-agent/personas/`](../hosted-agent/personas/) — Lisa
(an HR screener) is shipped as the example persona; swap her for any role by
pointing the `PERSONA_FILE` env var at a different Markdown file.

> **See also:** a full visual rendering of the architecture (every module,
> every transport, every Azure resource, every CI workflow) lives in
> [`diagrams/`](./diagrams/) as both `.excalidraw` and `.drawio`. The text
> below is the narrative version of the same scene.

## One picture

```
┌─────────────┐   1. schedule         ┌─────────────────────────────┐
│  Operator   │ ─────────────────────▶│   launcher (CLI / FastAPI)  │
│  (you)      │                       │                             │
└─────────────┘                       │  graph_client.create_       │
                                      │       teams_meeting()       │
                                      │  graph_client.send_         │
                                      │       interview_invite()    │
                                      │  bot_dispatcher.dispatch()  │
                                      └──────┬──────────┬───────────┘
                                             │          │
                                  2a. email  │          │ 3. dispatch (mode-dependent)
                                             ▼          ▼
                                  ┌────────────────┐  ┌─────────────────────────────┐
                                  │ Invitee inbox  │  │ Avatar transport (one of):  │
                                  │ (Outlook/etc.) │  │                             │
                                  └────────┬───────┘  │  A) graph_bot   (VMSS)      │
                                           │          │     └─▶ POST joinCall       │
                                           │          │                             │
                                           │          │  B) browser_webrtc (ACS)    │
                                           │          │     └─▶ latest-invite.json  │
                                           │          └──────────────┬──────────────┘
                                           │                         │
                                           ▼                         ▼
                                ┌──────────────────────────────────────────┐
                                │             Teams meeting                │
                                │   ┌──────────┐         ┌──────────────┐  │
                                │   │ Invitee  │ ◀ ─ ─ ▶ │ Avatar (Lisa)│  │
                                │   └──────────┘         └──────────────┘  │
                                └──────────────────────────────────────────┘
                                                  │
                                                  │ Voice Live (real-time
                                                  │ speech-to-speech)
                                                  ▼
                                ┌──────────────────────────────────────────┐
                                │  hosted-agent (Foundry-hosted container) │
                                │  - personas/lisa.md (editable prompt)    │
                                │  - tools/ (agent function tools)         │
                                │  - voice-live-config.json (voice/avatar) │
                                └──────────────────────────────────────────┘
```

## Components

### `core/`
Tiny shared building blocks consumed by both `launcher/` and `hosted-agent/`:

- `core.clients` — Foundry / Azure OpenAI client factories (tenant-aware
  `DefaultAzureCredential`).
- `core.config` — `AgentConfig` dataclass loaded from environment.

### `launcher/`
The CLI / thin service that an operator (or another app) calls to start a
session. Three small files:

| File | Role |
|---|---|
| `graph_client.py` | Microsoft Graph helpers — `create_teams_meeting`, `send_interview_invite`, `invite_bot_to_meeting`. Dual-mode auth (app-only first, delegated MSAL device-code fallback for tenants where the Application Access Policy hasn't propagated). The invite-email subject + body interpolate the `BRAND_NAME` env var (default: `Contoso`) so a fresh clone produces sensible copy without code edits. |
| `bot_dispatcher.py` | One `dispatch(join_url, mode=...)` call that routes to either `graph_bot` (VMSS) or `browser_webrtc` (ACS). |
| `cli.py` / `web.py` | Argparse front door + optional FastAPI wrapper. |

### `hosted-agent/`
The Foundry-hosted container that actually talks to Voice Live. Pure Python
agent — the persona is a Markdown file (`personas/lisa.md`), so non-developers
can edit instructions without touching Python. Voice + avatar character live
in `voice-live-config.json`.

Deployed via `agent-deploy.yml` to Azure Container Apps / Foundry, image stored
in your ACR.

### `bot/` (git submodule → `glejdis/teams-avatar-bot`)
The C# Microsoft Graph calling bot + Python sidecar that runs on the **VMSS**
transport. The bot accepts `POST /joinCall {joinURL, displayName}` and joins
the meeting as a regular Teams participant. The sidecar bridges Voice Live
audio frames into the Graph media stack.

This is the **production** path. It needs:
- A tenant-registered AAD bot app with the right Calls/Meetings permissions
- An Application Access Policy granting the bot rights to join meetings
- The VMSS instance reachable on a public FQDN (App Gateway + WAF in
  `infra/modules/`)

### `browser-fallback/` (FastAPI + ACS browser SDK)
The **local-dev** transport. A small FastAPI app serves a single-page operator
UI that uses the Azure Communication Services Web Calling SDK to join the
Teams meeting as an ACS guest from your browser. No bot registration, no
VMSS — just `python app.py` and open `http://localhost:3000`.

### `infra/`
Bicep templates for the production VMSS path: VNet, Key Vault (private
endpoint), VMSS (Windows, with NSSM-managed bot + sidecar services),
Application Gateway (WAF + TLS termination), Bastion, monitoring (Log
Analytics + alerts).

`main.bicep` is the entry point; `avatar-stack.bicep` wires up the
VMSS-specific resources (`scripts/vmss/install.ps1` is the bootstrap
extension).

## Sequence (production, `graph_bot` mode)

1. Operator runs `python -m launcher schedule --to alice@example.com --start +5 --mode graph_bot`.
2. `launcher.graph_client.create_teams_meeting()` calls Graph `POST /users/{organizer}/onlineMeetings`. Falls back to delegated `POST /me/onlineMeetings` if the app's policy hasn't propagated.
3. `launcher.graph_client.send_interview_invite()` calls Graph `POST /users/{organizer}/sendMail` with a branded HTML body containing the join URL.
4. `launcher.bot_dispatcher.dispatch()` POSTs `{joinURL, displayName, sessionId}` to `BOT_JOIN_ENDPOINT` (the App Gateway in front of VMSS). The C# bot validates `X-Bot-Auth`, places the Graph call, and pulls media from the sidecar.
5. The sidecar opens a Voice Live session against the Foundry agent (`AGENT_NAME` env var), and pumps the bidirectional audio + video stream through the Graph media SDK.
6. The invitee receives the email, clicks the link, joins Teams. The avatar is already in the meeting.

## Avatar, voice & background — the three layers

There are **three independent Voice Live sessions** in this repo, one per
runtime that can drive the avatar. Each session is opened by a different
process and therefore needs its own copy of the avatar / voice / background
config:

| # | Layer | Who opens the Voice Live session | Where config lives |
|---|---|---|---|
| 1 | **Foundry hosted-agent** | The Foundry runtime, when a client (the VMSS sidecar OR an external app) talks to the deployed agent | [`hosted-agent/voice-live-config.json`](../hosted-agent/voice-live-config.json) — attached to the agent metadata under `microsoft.voice-live.configuration` |
| 2 | **VMSS sidecar** (production `graph_bot` transport) | The Python sidecar process on the VMSS instance, talking to Voice Live directly (not via Foundry) | Bicep params → [`infra/avatar-stack.bicep`](../infra/avatar-stack.bicep) → [`infra/modules/vmss.bicep`](../infra/modules/vmss.bicep) → [`infra/vmss-bootstrap-extension.bicep`](../infra/vmss-bootstrap-extension.bicep) → [`scripts/vmss/install.ps1`](../scripts/vmss/install.ps1) → `AVATAR_*` env vars on the `lisa-sidecar` service |
| 3 | **Browser-fallback** (`browser_webrtc` transport) | The FastAPI process in `browser-fallback/` | [`browser-fallback/.env`](../browser-fallback/.env.example) (loaded by `app.py`) |

That separation is structural — each transport runs in a different security
boundary and there is no shared config store. The flip side is that when you
change "the avatar", you have to update whichever surface drives the session.

### Aligned defaults

All three layers ship with the same defaults so a first-time user sees the
same avatar regardless of transport:

| Setting | Default | Where to change |
|---|---|---|
| Voice | `en-US-AvaMultilingualNeural` | `voice-live-config.json` `voice.name` · `voiceLiveVoice` bicepparam · `AZURE_VOICELIVE_VOICE` env |
| Character | `lisa` | `voice-live-config.json` `avatar.character` · `avatarCharacter` bicepparam · `AVATAR_CHARACTER` env |
| Style | `casual-sitting` | `voice-live-config.json` `avatar.style` · `avatarStyle` bicepparam · `AVATAR_STYLE` env |
| Language | `en` | `voice-live-config.json` `input_audio_transcription.language` · `agentLanguage` bicepparam · (browser uses voice locale) |

If you want to swap the persona to something other than Lisa, point
`PERSONA_FILE` at a different Markdown file in `hosted-agent/personas/` and
re-deploy the agent. The avatar can stay as `lisa` (which is just the
Microsoft avatar character name and happens to share the persona's name) or
be swapped to any other character in the
[Microsoft avatar catalog](https://learn.microsoft.com/azure/ai-services/speech-service/text-to-speech-avatar/avatar-gestures-with-ssml).

### Background

Voice Live supports an **image** background (the service fetches a public
HTTPS URL and composites the avatar onto it) or a **solid color** background.
Image takes precedence if both are set; if neither is set the background is
the Voice Live default (transparent / chroma-keyable).

| Layer | Image URL | Color | How to set |
|---|---|---|---|
| Foundry hosted-agent | ✅ | ✅ | Edit `hosted-agent/voice-live-config.json`: replace `"background": { "color": "#FFFFFFFF" }` with `{ "image_url": "https://your-cdn/office.png" }` or any hex color. |
| VMSS sidecar | ✅ | ✅ | Set `avatarBackgroundImageUrl` / `avatarBackgroundColor` in `dev.bicepparam` / `prod.bicepparam`. They thread through to the sidecar as `AVATAR_BACKGROUND_IMAGE_URL` / `AVATAR_BACKGROUND_COLOR` env vars. |
| Browser-fallback | ✅ | ✅ | Set `AVATAR_BACKGROUND_IMAGE_URL` or `AVATAR_BACKGROUND_COLOR` in `browser-fallback/.env`. |

The browser-fallback page also draws an **independent client-side
chroma-key overlay** in a canvas, controlled by `AVATAR_CHROMA_ENABLED`,
`AVATAR_CHROMA_COLOR`, and `TEAMS_AVATAR_BACKGROUND_IMAGE`. That overlay is
drawn in the browser only — it replaces a green-screen background with a
local image, so it works with `localhost`-served images that Voice Live
itself cannot fetch. Use it for quick demos when you do not have a public
HTTPS URL handy.

## Region considerations

Voice Live + avatar streaming availability varies by region. Pick a Foundry
region that supports both (the defaults in `infra/main.bicep` are tuned for
Sweden Central / West Europe). If your region differs, also update
`voice-live-config.json` to a voice/avatar combo available there.
