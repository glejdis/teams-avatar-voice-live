# Architecture diagrams

Four equivalent renderings of the **teams_avatar_voice_live** end-to-end
architecture, kept in sync by a single generator script.

| File | What it's for | Open with |
|---|---|---|
| `architecture.png` | Inline preview in the main README | Any image viewer / GitHub |
| `architecture.svg` | Sharp vector for high-DPI / wikis | Any browser; GitHub renders inline via `<img>` |
| `architecture.excalidraw` | Hand-drawn / sketchy view, fast to tweak | [aka.ms/excalidraw](https://aka.ms/excalidraw) — File → Open |
| `architecture.drawio` | Auto-routed orthogonal view for wikis | [app.diagrams.net](https://app.diagrams.net) / draw.io desktop — File → Open |

The `.png` and `.svg` versions are a **simplified static preview**: a handful
of long cross-canvas dashed arrows (CI/CD → infra, VMSS → KV/Storage) are
omitted to keep the flat layout readable. The `.excalidraw` and `.drawio`
sources contain the **complete graph** (44 shapes, 37 edges) and are the
ones to edit if you need to add or rewire anything.

## What the diagram covers

- **Zone 0 — CI/CD (top strip):** the three GitHub Actions workflows
  (`infra-deploy`, `agent-deploy`, `secret-rotation`), the OIDC bootstrap, and
  the ACR they push to.
- **Zone 1 — Schedule path:** Operator → `launcher/` CLI/web → Microsoft Graph
  → invitee inbox → invitee clicks Join.
- **Zone 2 — Two transports:**
  - **A · `graph_bot`** — App Gateway → VMSS (C# EchoBot + Python avatar
    sidecar) joining as a first-class Teams participant.
  - **B · `browser_webrtc`** — `browser-fallback/app.py` serving an operator
    UI that joins as an ACS guest via the patched ACS Web Calling SDK, with
    an optional client-side chroma-key overlay.
- **Zone 3 — Teams meeting** (the convergence point where avatar + invitee
  meet).
- **Zone 4 — Voice Live + Foundry agent:** ACS / Voice Live runtime, plus
  the `hosted-agent/` container with `main.py`, `personas/`, `tools/`, and
  `voice-live-config.json`.
- **Bottom strip — Supporting Azure resources:** Key Vault (private endpoint),
  ACR, Storage Account, VNet, Bastion, Log Analytics — the contents of the
  `infra/` Bicep modules.

## Regenerating

All four files come out of a single Python generator that owns the layout +
edge list, so they always describe the same scene:

```powershell
# from the repo root
pip install matplotlib                  # one-time, needed for .png / .svg only
python scripts/gen_diagrams.py
```

Edit the `shapes` / `edges` lists at the top of `scripts/gen_diagrams.py` to
add nodes or rewire flows; all four files will stay in sync.
