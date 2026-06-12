# Browser-fallback transport (FastAPI + ACS browser SDK)

This is the **browser** transport for the avatar — the operator opens a small
FastAPI page in their browser, the page joins the Teams meeting through Azure
Communication Services (ACS) as a Teams guest, and Azure Voice Live drives the
avatar audio (and optionally a chroma-keyed avatar video overlay) into the
call.

It is the **easy** path: no bot registration, no VMSS, no App Gateway. The
trade-off is that the browser tab has to stay open for the call and the
participant shows up as an ACS guest display name (not a tenant identity). See
[`docs/architecture.md`](../docs/architecture.md) for the pros and cons vs the
VMSS `graph_bot` path.

## Run locally

From the `browser-fallback/` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# Edit .env and fill in AZURE_VOICELIVE_ENDPOINT +
# AZURE_COMMUNICATION_CONNECTION_STRING at minimum.

python app.py
```

Open `http://localhost:3000` in Edge or Chrome. The ACS browser SDK rejects
`http://127.0.0.1` for Teams calling, so use `localhost` even though the page
itself loads on either. Override the port with `PORT=4000 python app.py`.

## Driving a session end to end

The browser page works hand in hand with the `launcher` CLI:

1. **Schedule a meeting.** From a second terminal (or the same machine):

    ```powershell
    python -m launcher schedule --to alice@example.com --start +5
    ```

    `launcher.schedule` calls Microsoft Graph to create a Teams meeting, emails
    the invite to the recipient, and writes `browser-fallback/data/latest-invite.json`
    with the join URL.

2. **Pick up the invite in the browser.** The page polls
    `latest-invite.json` and auto-fills the Teams meeting link. If you prefer to
    drive it manually, paste any Teams meeting link directly into the field.

3. **Click `Join Teams`.** The page first opens a Voice Live WebSocket against
    the configured Foundry agent (or the inline-instructions fallback if no
    agent is set), waits for avatar audio readiness, and then joins the Teams
    call as an ACS guest. Verify both directions:

    - **Outbound (avatar → Teams):** a short typed prompt in the operator page
      is the fastest check that participants in Teams hear the avatar.
    - **Inbound (Teams → avatar):** speak from a Teams client. Do not use the
      page's own `Mic` while testing — Teams is the only mic source on this
      path.

## Avatar configuration

Voice, avatar character, and avatar background are all controlled via env vars
in `browser-fallback/.env`. See `.env.example` for the full list. The defaults
that ship are tuned for the **Lisa** persona shipped in
`hosted-agent/personas/lisa.md`:

| Variable | Default | What it does |
|---|---|---|
| `AZURE_VOICELIVE_VOICE` | `en-US-AvaMultilingualNeural` | The TTS voice. Any Azure neural voice in your Voice Live region. |
| `AVATAR_CHARACTER` / `AVATAR_STYLE` | `lisa` / `casual-sitting` | The avatar character and pose. See the [Microsoft avatar catalog](https://learn.microsoft.com/azure/ai-services/speech-service/text-to-speech-avatar/avatar-gestures-with-ssml). |
| `AVATAR_BACKGROUND_IMAGE_URL` | _empty_ | Public HTTPS URL the Voice Live service fetches server-side and uses as the avatar background. Image takes precedence over color. |
| `AVATAR_BACKGROUND_COLOR` | _empty_ | Hex color (e.g. `"#FFFFFFFF"`) used when no image URL is set. Empty = Voice Live default (transparent). |
| `AVATAR_CHROMA_ENABLED` / `AVATAR_CHROMA_COLOR` / `TEAMS_AVATAR_BACKGROUND_IMAGE` | `true` / `#00ff00` / `/office-background.png` | Client-side canvas chroma-key overlay. The page draws the local background image behind the green-screened avatar in the browser — independent of the server-side `AVATAR_BACKGROUND_*` settings above. |

## Letting Lisa see a shared screen

When this is enabled, Lisa can look at and comment on a screen that the other
participant is sharing in the Teams meeting. Voice Live itself does not accept
video input, so the browser-fallback server captures one JPEG frame from the
remote screen-share stream, calls a multimodal Azure OpenAI deployment for a
short description, and injects that description as a synthetic user turn into
Lisa's running Voice Live session — Lisa then speaks the comment through the
avatar.

### Setup (~2 minutes)

By default we reuse the same Foundry / Azure OpenAI resource Lisa already
runs against. `gpt-4.1-mini` is multimodal, so no extra deployment is needed.
Auth uses `DefaultAzureCredential` (i.e. `az login`) like the rest of this
package.

Add (or uncomment) the following in `browser-fallback/.env`:

```bash
# Optional: explicit endpoint. Leave empty to derive from AZURE_VOICELIVE_ENDPOINT.
# AZURE_OPENAI_VISION_ENDPOINT=https://<FOUNDRY_ACCOUNT_NAME>.openai.azure.com
AZURE_OPENAI_VISION_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_VISION_API_VERSION=2024-12-01-preview
# Optional: API key instead of AAD.
# AZURE_OPENAI_VISION_API_KEY=

VISION_AUTO_ON_SHARE_START=true   # Lisa comments once when a new share begins
VISION_PERIODIC_INTERVAL_S=0      # No periodic loop in the POC
VISION_MAX_IMAGE_DIM=512          # Downsample longest edge before sending
```

Restart `python app.py`. `GET /api/config` should return `"visionEnabled": true`.

### Triggers

| Trigger | When it fires |
|---|---|
| `share_start` | Once when a new remote screen-share stream is detected (auto, controlled by `VISION_AUTO_ON_SHARE_START`). |
| `on_demand` (button) | Operator clicks **Show Lisa my screen** in the controls panel. |
| `on_demand` (voice) | The candidate says something matching "look at / see / check / read / view … screen / slide / page / window / tab / document". 8-second cooldown so repeated mentions don't double-fire. |

Periodic capture (`VISION_PERIODIC_INTERVAL_S > 0`) is intentionally **off** in
the POC — without a perceptual-hash gate it spends tokens narrating unchanged
slides. Build that gate before turning it on.

### What it looks like in the UI

A new pill in the status strip shows `Vision: idle | watching | share active | thinking | spoke | error`.
A new button **Show Lisa my screen** is enabled while a Teams call is connected
and a share is active.

### Privacy notes

- Captured frames are kept in memory only, keyed by the Teams Voice Live
  session id, and cleared when that session ends. They are never persisted to
  disk.
- The vision call goes to **your** Azure OpenAI resource — no third parties.
- The system prompt instructs Lisa to **summarize** rather than read URLs,
  email addresses, phone numbers, or long numbers verbatim. Tune the prompt
  in `vision.py` (`_DEFAULT_SYSTEM_PROMPT`) if your scenario needs stricter
  rules.
- If you ship this beyond a POC, declare it in the persona's opening line —
  e.g. _"By the way, I'll be able to see your screen if you share it during
  this session."_

### Endpoints (for debugging)

| Method · path | Purpose |
|---|---|
| `POST /api/vision/screen-frame` | Body: `{ clientId, jpegBase64, trigger }`. Stores the frame and, unless `trigger === "buffer_only"`, runs the vision call and pushes Lisa's spoken comment. Returns `{ ok, stored, spoke, description, reason? }`. |
| `GET /api/vision/latest-frame?client_id=…` | Returns the most recent JPEG buffered for that client. Handy for diagnosing "why didn't Lisa say anything?". |

### Known limitations

- No multi-presenter handoff beyond what ACS emits as fresh `videoStreamsUpdated` events.
- Vision round-trip is ~1.5–3s. Lisa pauses noticeably between the cue and her spoken comment — the `Vision: thinking` pill makes this visible to the operator.
- Some tenants block ACS guests from receiving screen-share content entirely. If you see `Vision: no share` while a Teams participant is clearly sharing, that's the likely cause — switch to the `graph_bot` transport (which is a first-class Teams participant).

## Operational notes

- The Teams participant appears as an ACS guest display name (set with
  `TEAMS_DISPLAY_NAME`), not a tenant identity. Teams may show
  `Limited call features` — that is normal for ACS guests and does not by
  itself mean audio is broken.
- The browser tab must stay open and the laptop awake for the duration of the
  call. WebRTC keeps running when the tab loses focus, but `Sleep` will drop it.
- Use headphones if the Teams client and operator page run on the same
  machine, to prevent echo. Always speak through Teams, not the page mic.
- The page makes one automatic Voice Live reconnect attempt if the
  Voice Live WebSocket drops mid-call. Teams stays connected during the
  reconnect.
- The visible red `Leave Meeting` button hangs up Teams and stops Voice Live
  in one click.

## Logs and artifacts

| File | What it is |
|---|---|
| `browser-fallback/data/latest-invite.json` | Written by `launcher.schedule`; read by the page. |
| `browser-fallback/data/auto-join-decisions.jsonl` | Per-invite decision the auto-join arming made (when enabled). |
| `browser-fallback/data/operator-events.jsonl` | Page lifecycle events. The page can also download its in-browser log as `.txt`. |
| `browser-fallback/data/transcripts.json` | Conversation transcripts written by the page (when transcripts are enabled). |

Override paths via the `BROWSER_DEMO_*_PATH` env vars in `.env.example`.
