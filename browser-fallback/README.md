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
| `AVATAR_CHROMA_ENABLED` / `AVATAR_CHROMA_COLOR` / `TEAMS_AVATAR_BACKGROUND_IMAGE` | `true` / `#00ff00` / `/background.png` | Client-side canvas chroma-key overlay. The page draws the local background image behind the green-screened avatar in the browser — independent of the server-side `AVATAR_BACKGROUND_*` settings above. |

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
