"""Generate teams_avatar_voice_live architecture diagrams.

Produces four equivalent renderings of the same scene:
  - docs/diagrams/architecture.excalidraw  (Excalidraw JSON, hand-drawn look)
  - docs/diagrams/architecture.drawio       (draw.io / diagrams.net XML)
  - docs/diagrams/architecture.svg          (clean vector, GitHub-renderable)
  - docs/diagrams/architecture.png          (raster preview for README)

Re-run after editing this file:
    python artifacts/gen_diagrams.py
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# ────────────────────────────────────────────────────────────────────────────
# OUTPUT PATHS
# ────────────────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
OUT  = REPO / "docs" / "diagrams"
OUT.mkdir(parents=True, exist_ok=True)

# ────────────────────────────────────────────────────────────────────────────
# COLOR PALETTE (mostly Open Color, FluentUI accents for MS-flavoured nodes)
# ────────────────────────────────────────────────────────────────────────────
C = {
    "human":     ("#e67700", "#ffe8cc"),   # orange — humans
    "code":      ("#1864ab", "#a5d8ff"),   # blue — Python modules in the repo
    "graph":     ("#862e9c", "#f3d9fa"),   # purple — Microsoft Graph
    "transport": ("#0c8599", "#99e9f2"),   # teal — both transport implementations
    "azure":     ("#0078d4", "#cfe4fa"),   # MS blue — Azure resources
    "voicelive": ("#2f9e44", "#b2f2bb"),   # green — Voice Live + Foundry runtime
    "teams":     ("#5c2d91", "#e8daee"),   # MS purple — Teams meeting
    "ci":        ("#495057", "#dee2e6"),   # gray — CI/CD
    "infra":     ("#1971c2", "#dbe4ff"),   # supporting Azure infra
    "host_box":  ("#1e1e1e", "transparent"),  # container outlines
}
TEXT = "#000000"

# ────────────────────────────────────────────────────────────────────────────
# SCENE — explicit list of shapes + edges so both formats stay in sync
# ────────────────────────────────────────────────────────────────────────────
# Layout grid: 1740 wide × 1500 tall, 20-px grid.
shapes: list[dict] = []
edges: list[dict] = []

def S(id, x, y, w, h, label, kind, *, dashed=False, font=14):
    shapes.append(dict(id=id, x=x, y=y, w=w, h=h, label=label, kind=kind,
                       dashed=dashed, font=font))

def E(src, dst, label="", *, color="#212529", dashed=False):
    edges.append(dict(src=src, dst=dst, label=label, color=color, dashed=dashed))

# ── Title / subtitle (free text, rendered as Excalidraw text elements) ─────
TITLE = ("teams_avatar_voice_live — End-to-End Architecture (v0.1.3)",
         "AI agent joins a Microsoft Teams meeting as a video avatar driven by Azure Voice Live")

# ── Zone 0: CI/CD compact strip (top) ──────────────────────────────────────
S("ci_box", 40, 120, 1660, 80,
  "Build & Deploy  ·  GitHub Actions (OIDC → Azure)",
  "host_box", dashed=True, font=14)
S("wf_infra",   80, 150, 320, 40, ".github/workflows/infra-deploy.yml\nBicep what-if + apply", "ci", font=11)
S("wf_agent",  420, 150, 320, 40, ".github/workflows/agent-deploy.yml\nbuilds hosted-agent + VMSS artifact zip", "ci", font=11)
S("wf_secret", 760, 150, 320, 40, ".github/workflows/secret-rotation.yml\nrotates KV secrets on cron", "ci", font=11)
S("oidc",     1100, 150, 280, 40, "scripts/bootstrap-oidc.ps1\nfederated UAMI per (env, repo)", "ci", font=11)
S("acr_push", 1400, 150, 280, 40, "Azure Container Registry\nhosted-agent image + bot image", "azure", font=11)

# ── Zone 1: Operator → launcher → Graph → Invitee (Y=240-560) ──────────────
S("operator", 60, 320, 160, 70, "Operator\n(human)", "human", font=14)

S("launcher_box", 260, 240, 720, 320,
  "launcher/  (Python CLI, optional FastAPI wrapper)\n`pip install -e .[web]`",
  "host_box", dashed=True, font=14)
S("cli",      300, 300, 200, 70, "cli.py\npython -m launcher\nschedule | dispatch", "code", font=12)
S("web",      520, 300, 200, 70, "web.py\nuvicorn launcher.web:app\nPOST /api/schedule", "code", font=12)
S("graphcli", 300, 390, 200, 90, "graph_client.py\ncreate_teams_meeting\nsend_interview_invite\n(BRAND_NAME env var)", "code", font=12)
S("dispatch", 520, 390, 200, 90, "bot_dispatcher.py\ndispatch(join_url, mode=)\n→ graph_bot or\n   browser_webrtc", "code", font=12)
S("core",     740, 300, 220, 180, "core/\nclients.py — Foundry +\n  Azure OpenAI client factories\nconfig.py — AgentConfig\n(DefaultAzureCredential)", "code", font=12)

S("msgraph", 1020, 320, 240, 90, "Microsoft Graph API\n/users/{org}/onlineMeetings\n/users/{org}/sendMail\ncommunications.calls", "graph", font=12)

S("inbox", 1310, 280, 200, 70, "Invitee inbox\n(Outlook / Gmail / …)", "azure", font=12)
S("invitee", 1310, 380, 200, 80, "Invitee\n(human, clicks the\nTeams Join link)", "human", font=13)

# ── Zone 2: Two transports side by side (Y=600-960) ─────────────────────────
S("ta_box", 40, 600, 820, 360,
  "Transport A — graph_bot  (production / VMSS)",
  "host_box", dashed=True, font=14)
S("app_gw",   80, 660, 340, 70, "App Gateway (WAF) + TLS\npublic FQDN  =  BOT_JOIN_ENDPOINT", "azure", font=12)
S("ta_auth", 440, 660, 380, 70, "POST /joinCall + X-Bot-Auth\n{joinURL, displayName, sessionId}", "transport", font=12)
S("vmss_outer", 80, 760, 740, 180, "VMSS  (Windows Server 2022, NSSM-managed services)", "host_box", dashed=True, font=13)
S("echo_bot", 100, 810, 350, 110, "C# Echo Bot  (bot/ submodule)\nMicrosoft.Skype.Bots.Media\n→ places Graph call\n→ joins as 1st-class Teams participant", "transport", font=12)
S("sidecar",  460, 810, 350, 110, "Python avatar-sidecar  (bot/avatar-sidecar/)\nbridges Voice Live audio frames\n↔ Graph Media SDK\nNSSM service: lisa-sidecar", "transport", font=12)

S("tb_box", 880, 600, 820, 360,
  "Transport B — browser_webrtc  (local dev / 30-sec demo)",
  "host_box", dashed=True, font=14)
S("browser_app", 920, 660, 340, 70, "browser-fallback/app.py\nFastAPI on http://localhost:3000\nstatic/ operator UI", "transport", font=12)
S("inv_json",   1280, 660, 380, 70, "browser-fallback/data/latest-invite.json\n(written by launcher · polled by static page)", "transport", font=12)
S("op_browser_outer", 920, 760, 740, 180, "Operator browser tab  (Edge / Chrome on http://localhost:3000)", "host_box", dashed=True, font=13)
S("acs_sdk",  940, 810, 350, 110, "ACS Web Calling SDK  (patched)\nrebuild-acs/ — getMediaStream fix\njoinMeeting as ACS guest\n(WebRTC media)", "transport", font=12)
S("chroma",  1300, 810, 350, 110, "Chroma-key canvas overlay\nAVATAR_CHROMA_* env vars\nlocal background composite\n(client-side only)", "transport", font=12)

# ── Zone 3: Teams meeting (the convergence point) ───────────────────────────
S("teams", 420, 1000, 920, 80,
  "Microsoft Teams meeting\nInvitee  ↔  Avatar (Lisa)  —  bidirectional audio + video",
  "teams", font=15)

# ── Zone 4: Voice Live + Foundry (Y=1120-1400) ──────────────────────────────
S("foundry_box", 200, 1120, 1360, 320,
  "Foundry project  (Azure AI Services / hub)",
  "host_box", dashed=True, font=14)
S("voicelive", 240, 1170, 560, 80, "Azure Voice Live\nreal-time speech-to-speech\non top of Azure OpenAI / Foundry", "voicelive", font=13)
S("acs_svc",   820, 1170, 720, 80, "Azure Communication Services (ACS)\nTeams meeting bridge for Transport B (browser path)", "azure", font=13)
S("ha_outer",  240, 1270, 1300, 160, "hosted-agent container  (Foundry-hosted, port 8088, Responses protocol)", "host_box", dashed=True, font=13)
S("main_py", 260, 1320, 300, 100, "main.py\nAgent factory\nregisters example tools\nEXPOSE 8088", "voicelive", font=12)
S("personas", 580, 1320, 300, 100, "personas/\nlisa.md (worked example)\ngeneric.md (starter)\nPERSONA_FILE env var", "voicelive", font=12)
S("tools",  900, 1320, 300, 100, "tools/\njob_requirements.py\n(example knowledge tool —\nswap for your domain)", "voicelive", font=12)
S("vcfg", 1220, 1320, 300, 100, "voice-live-config.json\nvoice / VAD / avatar\nattached as agent metadata\n(microsoft.voice-live.configuration)", "voicelive", font=12)

# ── Bottom strip: supporting Azure resources (infra/) ──────────────────────
S("infra_box", 40, 1470, 1660, 80,
  "Supporting Azure resources  (infra/  — Bicep)",
  "host_box", dashed=True, font=14)
S("kv",   60, 1500, 270, 40, "Key Vault  (private endpoint)\nbot client secret + TLS cert", "infra", font=11)
S("acr2",340, 1500, 270, 40, "ACR  (image registry)\nhosted-agent + bot images", "infra", font=11)
S("stor",620, 1500, 270, 40, "Storage Account\nagent-artifacts/<sha>.zip", "infra", font=11)
S("net", 900, 1500, 240, 40, "VNet + subnets\nNSGs · service endpoints", "infra", font=11)
S("bast",1150, 1500, 240, 40, "Bastion  (break-glass)\noff by default", "infra", font=11)
S("logs",1400, 1500, 300, 40, "Log Analytics + Alerts\nVMSS · App Gw · sidecar logs", "infra", font=11)

# ────────────────────────────────────────────────────────────────────────────
# EDGES (data flow)
# ────────────────────────────────────────────────────────────────────────────
# CI/CD → runtime targets
E("wf_infra",  "infra_box", "what-if + apply", dashed=True, color="#495057")
E("wf_agent",  "ha_outer", "build + push image", dashed=True, color="#495057")
E("wf_agent",  "vmss_outer", "publishes artifact zip", dashed=True, color="#495057")
E("wf_secret", "kv", "rotates secrets", dashed=True, color="#495057")
E("acr_push",  "ha_outer", "pulls image", dashed=True, color="#495057")

# Operator → launcher
E("operator", "cli", "schedule --to alice@…")

# launcher internals
E("cli",      "graphcli", "")
E("cli",      "dispatch", "")
E("web",      "graphcli", "")
E("web",      "dispatch", "")
E("graphcli", "core", "client factory")
E("dispatch", "core", "")

# launcher → Graph → Inbox → Invitee → Teams
E("graphcli", "msgraph", "REST + delegated fallback")
E("msgraph",  "inbox",   "sendMail")
E("inbox",    "invitee", "email arrives")
E("invitee",  "teams",   "clicks Join (Teams desktop / web)", color="#1864ab")

# Transport A
E("dispatch", "app_gw", "POST /joinCall (Transport A)", color="#0c8599")
E("app_gw",   "ta_auth", "TLS terminate", color="#0c8599")
E("ta_auth",  "echo_bot", "X-Bot-Auth", color="#0c8599")
E("echo_bot", "sidecar", "local media bridge", color="#0c8599")
E("sidecar",  "voicelive", "Voice Live WS  (audio frames)", color="#2f9e44")
E("echo_bot", "teams", "joins as Teams bot", color="#5c2d91")

# Transport B
E("dispatch", "inv_json", "writes (Transport B)", color="#0c8599")
E("inv_json", "browser_app", "served on /data/…", color="#0c8599")
E("browser_app", "op_browser_outer", "serves /static/", color="#0c8599")
E("acs_sdk",  "acs_svc", "ACS join (Web Calling SDK)", color="#0c8599")
E("acs_svc",  "teams",   "joins as ACS guest", color="#5c2d91")
E("acs_sdk",  "voicelive", "Voice Live WS  (avatar audio)", color="#2f9e44")

# Voice Live ↔ hosted-agent
E("voicelive", "ha_outer", "agent inference (Responses)", color="#2f9e44")
E("ha_outer",  "main_py", "loads", dashed=True)
E("main_py",   "personas", "instructions.py reads", dashed=True)
E("main_py",   "tools", "register_*", dashed=True)
E("ha_outer",  "vcfg", "attached to agent version", dashed=True)

# VMSS → KV (runtime secrets)
E("vmss_outer", "kv", "reads at boot (MI)", dashed=True, color="#1971c2")
E("vmss_outer", "stor", "pulls artifact zip", dashed=True, color="#1971c2")
E("vmss_outer", "acr2", "pulls bot image", dashed=True, color="#1971c2")
E("vmss_outer", "logs", "streams logs", dashed=True, color="#1971c2")

# ════════════════════════════════════════════════════════════════════════════
# EXCALIDRAW EMITTER
# ════════════════════════════════════════════════════════════════════════════
def excalidraw() -> dict:
    elements = []
    # Title + subtitle
    elements.append(dict(type="text", id="title", x=40, y=20, width=1660, height=44,
                         text=TITLE[0], fontSize=26, fontFamily=2,
                         strokeColor=TEXT, textAlign="center", verticalAlign="top"))
    elements.append(dict(type="text", id="subtitle", x=40, y=70, width=1660, height=28,
                         text=TITLE[1], fontSize=14, fontFamily=2,
                         strokeColor=TEXT, textAlign="center", verticalAlign="top"))

    # Shapes
    for s in shapes:
        stroke, fill = C[s["kind"]]
        is_container = (s["kind"] == "host_box")
        rect_id = s["id"]
        text_id = f"{rect_id}__t"
        elements.append({
            "type": "rectangle",
            "id": rect_id,
            "x": s["x"], "y": s["y"], "width": s["w"], "height": s["h"],
            "strokeColor": stroke,
            "backgroundColor": fill,
            "fillStyle": "solid",
            "strokeWidth": 3 if is_container else 2,
            "strokeStyle": "dashed" if s["dashed"] else "solid",
            "roundness": {"type": 3},
            "seed": (hash(rect_id) & 0x7fffffff),
            "boundElements": [{"type": "text", "id": text_id}],
        })
        elements.append({
            "type": "text",
            "id": text_id,
            "x": s["x"], "y": s["y"], "width": s["w"], "height": s["h"],
            "text": s["label"],
            "fontSize": s["font"],
            "fontFamily": 2,  # Helvetica
            "strokeColor": TEXT,
            "textAlign": "center",
            "verticalAlign": "top" if is_container else "middle",
            "containerId": rect_id,
            "seed": (hash(text_id) & 0x7fffffff),
        })

    # Edges — connect by binding to the source / target rectangle centres.
    by_id = {s["id"]: s for s in shapes}
    for i, e in enumerate(edges):
        src = by_id[e["src"]]
        dst = by_id[e["dst"]]
        sx = src["x"] + src["w"] / 2
        sy = src["y"] + src["h"] / 2
        dx = dst["x"] + dst["w"] / 2
        dy = dst["y"] + dst["h"] / 2
        eid = f"edge_{i}"
        elements.append({
            "type": "arrow",
            "id": eid,
            "x": sx, "y": sy,
            "width": dx - sx, "height": dy - sy,
            "strokeColor": e["color"],
            "backgroundColor": "transparent",
            "fillStyle": "solid",
            "strokeWidth": 2,
            "strokeStyle": "dashed" if e["dashed"] else "solid",
            "roundness": None,
            "points": [[0, 0], [dx - sx, dy - sy]],
            "startBinding": {"elementId": e["src"], "focus": 0, "gap": 4},
            "endBinding":   {"elementId": e["dst"], "focus": 0, "gap": 4},
            "startArrowhead": None,
            "endArrowhead": "arrow",
            "seed": (hash(eid) & 0x7fffffff),
        })
        if e["label"]:
            lid = f"edge_label_{i}"
            label_text = e["label"]
            label_w = max(140, len(label_text) * 7)
            elements.append({
                "type": "text",
                "id": lid,
                "x": (sx + dx) / 2 - label_w / 2,
                "y": (sy + dy) / 2 - 9,
                "width": label_w, "height": 18,
                "text": label_text,
                "fontSize": 11,
                "fontFamily": 2,
                "strokeColor": TEXT,
                "backgroundColor": "#ffffff",
                "textAlign": "center",
                "verticalAlign": "middle",
                "seed": (hash(lid) & 0x7fffffff),
            })
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://github.com/glejdis/teams_avatar_voice_live",
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": 20},
        "files": {},
    }


# ════════════════════════════════════════════════════════════════════════════
# DRAW.IO EMITTER
# ════════════════════════════════════════════════════════════════════════════
def drawio() -> str:
    """Emit a mxGraphModel XML compatible with draw.io / diagrams.net."""
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(f'<mxfile host="app.diagrams.net" agent="teams_avatar_voice_live/gen_diagrams.py" version="24.4.0">')
    parts.append('  <diagram id="architecture" name="Architecture">')
    parts.append('    <mxGraphModel dx="2200" dy="1600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
                 'arrows="1" fold="1" page="1" pageScale="1" pageWidth="1750" pageHeight="1600" math="0" shadow="0">')
    parts.append('      <root>')
    parts.append('        <mxCell id="0" />')
    parts.append('        <mxCell id="1" parent="0" />')

    next_id = [10]
    def nid():
        next_id[0] += 1
        return f"n{next_id[0]}"

    # Title + subtitle (as text-only mxCells)
    title_id = nid()
    parts.append(f'        <mxCell id="{title_id}" value="{xml_escape(TITLE[0])}" '
                 f'style="text;html=1;align=center;verticalAlign=top;fontSize=22;fontStyle=1" vertex="1" parent="1">'
                 f'<mxGeometry x="40" y="20" width="1660" height="40" as="geometry"/></mxCell>')
    sub_id = nid()
    parts.append(f'        <mxCell id="{sub_id}" value="{xml_escape(TITLE[1])}" '
                 f'style="text;html=1;align=center;verticalAlign=top;fontSize=13" vertex="1" parent="1">'
                 f'<mxGeometry x="40" y="65" width="1660" height="30" as="geometry"/></mxCell>')

    # Shapes — drawio styles keyed off `kind`.
    def shape_style(kind, dashed):
        stroke, fill = C[kind]
        if kind == "host_box":
            base = (f"rounded=1;arcSize=4;whiteSpace=wrap;html=1;fillColor=none;"
                    f"strokeColor={stroke};strokeWidth=2;verticalAlign=top;"
                    f"fontSize=14;fontStyle=1;align=left;spacingLeft=8;spacingTop=4;")
        else:
            base = (f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor={fill};"
                    f"strokeColor={stroke};strokeWidth=2;fontSize=12;fontColor=#000000;"
                    f"verticalAlign=middle;align=center;spacing=4;")
        if dashed:
            base += "dashed=1;"
        return base

    for s in shapes:
        style = shape_style(s["kind"], s["dashed"])
        # draw.io uses \n for newlines inside value, and we want HTML escape
        val = xml_escape(s["label"])
        # In drawio xml, line breaks need to be literal &#10;
        val = val.replace("\n", "&#10;")
        parts.append(f'        <mxCell id="{s["id"]}" value="{val}" style="{style}" vertex="1" parent="1">'
                     f'<mxGeometry x="{s["x"]}" y="{s["y"]}" width="{s["w"]}" height="{s["h"]}" as="geometry"/></mxCell>')

    # Edges
    for i, e in enumerate(edges):
        eid = f"e{i}"
        style = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
                 f"strokeColor={e['color']};strokeWidth=2;fontSize=10;labelBackgroundColor=#ffffff;")
        if e["dashed"]:
            style += "dashed=1;"
        val = xml_escape(e["label"]) if e["label"] else ""
        parts.append(f'        <mxCell id="{eid}" value="{val}" style="{style}" '
                     f'edge="1" parent="1" source="{e["src"]}" target="{e["dst"]}">'
                     f'<mxGeometry relative="1" as="geometry"/></mxCell>')

    parts.append('      </root>')
    parts.append('    </mxGraphModel>')
    parts.append('  </diagram>')
    parts.append('</mxfile>')
    return "\n".join(parts)


# ════════════════════════════════════════════════════════════════════════════
# MATPLOTLIB EMITTER  (clean PNG + SVG for README preview)
# ════════════════════════════════════════════════════════════════════════════
# Edges to omit from the static PNG/SVG (they span large Y distances and add
# more visual noise than information in a flat view — the full unfiltered
# graph is still in the .excalidraw / .drawio sources).
STATIC_EDGE_SKIP_PREFIXES = {
    # CI/CD strip → runtime / infra strip
    ("wf_infra", "infra_box"),
    ("wf_agent", "ha_outer"),
    ("wf_agent", "vmss_outer"),
    ("wf_secret", "kv"),
    ("acr_push", "ha_outer"),
    # VMSS → infra strip (managed-identity reads, log streaming)
    ("vmss_outer", "kv"),
    ("vmss_outer", "stor"),
    ("vmss_outer", "acr2"),
    ("vmss_outer", "logs"),
}


def _orthogonal_path(src: dict, dst: dict) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """Return an L-shaped path from src rect edge to dst rect edge, plus a
    midpoint suitable for the edge label."""
    scx, scy = src["x"] + src["w"] / 2, src["y"] + src["h"] / 2
    dcx, dcy = dst["x"] + dst["w"] / 2, dst["y"] + dst["h"] / 2
    dx, dy = dcx - scx, dcy - scy
    horizontal_first = abs(dx) >= abs(dy)

    def edge_point(rect, ox, oy):
        cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
        ddx, ddy = ox - cx, oy - cy
        if ddx == 0 and ddy == 0:
            return cx, cy
        hw, hh = rect["w"] / 2, rect["h"] / 2
        tx = hw / abs(ddx) if ddx else float("inf")
        ty = hh / abs(ddy) if ddy else float("inf")
        t = min(tx, ty)
        return cx + ddx * t, cy + ddy * t

    if horizontal_first:
        corner = (dcx, scy)
        start_anchor = (scx + (dst["x"] - scx if dx > 0 else 0), scy)  # placeholder
    else:
        corner = (scx, dcy)
        start_anchor = (scx, scy + (dst["y"] - scy if dy > 0 else 0))

    if horizontal_first:
        start = edge_point(src, dcx, scy)
        end   = edge_point(dst, dcx, scy)
    else:
        start = edge_point(src, scx, dcy)
        end   = edge_point(dst, scx, dcy)
    pts = [start, corner, end]
    label_pt = corner
    return pts, label_pt


def matplotlib_render(out_path: Path) -> None:
    """Render the scene as a clean, flat SVG / PNG via matplotlib.

    Output format is determined by the file extension. Uses the diagram's own
    coordinate system (1740-wide canvas, y increases downward). Long
    cross-canvas dashed arrows are omitted from this view to keep it
    readable — see the .excalidraw / .drawio sources for the full graph.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import PathPatch

    CANVAS_W, CANVAS_H = 1740, 1580
    fig = plt.figure(figsize=(CANVAS_W / 100, CANVAS_H / 100), dpi=120)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, CANVAS_W)
    ax.set_ylim(CANVAS_H, 0)
    ax.set_aspect("equal")
    ax.set_axis_off()
    fig.patch.set_facecolor("white")

    ax.text(CANVAS_W / 2, 40, TITLE[0],
            ha="center", va="center", fontsize=17, fontweight="bold", color=TEXT)
    ax.text(CANVAS_W / 2, 78, TITLE[1],
            ha="center", va="center", fontsize=9.5, color="#444444")
    ax.text(CANVAS_W / 2, 102,
            "Static preview · L-shape routing · long cross-canvas dashed arrows "
            "(CI/CD ⇄ infra) omitted for readability — see .excalidraw / .drawio "
            "sources for the full graph",
            ha="center", va="center", fontsize=7.5, color="#888888", style="italic")

    by_id = {s["id"]: s for s in shapes}

    container_shapes = [s for s in shapes if s["kind"] == "host_box"]
    leaf_shapes      = [s for s in shapes if s["kind"] != "host_box"]

    def draw_shape(s: dict) -> None:
        stroke, fill = C[s["kind"]]
        is_container = (s["kind"] == "host_box")
        face = "none" if fill == "transparent" else fill
        linestyle = "--" if s["dashed"] else "-"
        patch = FancyBboxPatch(
            (s["x"], s["y"]), s["w"], s["h"],
            boxstyle="round,pad=0,rounding_size=10",
            linewidth=2.2 if is_container else 1.4,
            edgecolor=stroke,
            facecolor=face,
            linestyle=linestyle,
            zorder=1 if is_container else 3,
        )
        ax.add_patch(patch)
        if is_container:
            ax.text(s["x"] + 12, s["y"] + 9, s["label"],
                    ha="left", va="top",
                    fontsize=max(8.5, s["font"] - 2.5),
                    fontweight="bold",
                    color=stroke, zorder=2)
        else:
            ax.text(s["x"] + s["w"] / 2, s["y"] + s["h"] / 2, s["label"],
                    ha="center", va="center",
                    fontsize=max(6.8, s["font"] - 3.5),
                    color=TEXT, zorder=4,
                    linespacing=1.25)

    for s in container_shapes:
        draw_shape(s)
    for s in leaf_shapes:
        draw_shape(s)

    # Filtered edges with orthogonal routing
    for e in edges:
        if (e["src"], e["dst"]) in STATIC_EDGE_SKIP_PREFIXES:
            continue
        src = by_id[e["src"]]
        dst = by_id[e["dst"]]
        pts, label_pt = _orthogonal_path(src, dst)

        # Draw L-path as a Path, then add an arrowhead at the tip via a tiny
        # zero-length FancyArrowPatch at the end segment.
        codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(pts) - 1)
        path = MplPath(pts, codes)
        ax.add_patch(PathPatch(
            path, facecolor="none", edgecolor=e["color"],
            linewidth=1.2,
            linestyle="--" if e["dashed"] else "-",
            zorder=5,
        ))
        # Arrowhead on the last segment
        p_pre, p_end = pts[-2], pts[-1]
        ax.add_patch(FancyArrowPatch(
            p_pre, p_end,
            arrowstyle="-|>", mutation_scale=10,
            linewidth=0, color=e["color"], zorder=6,
            shrinkA=0, shrinkB=0,
        ))

        if e["label"]:
            # Place label on the longer of the two segments to avoid clutter
            seg1_len = abs(pts[1][0] - pts[0][0]) + abs(pts[1][1] - pts[0][1])
            seg2_len = abs(pts[2][0] - pts[1][0]) + abs(pts[2][1] - pts[1][1])
            if seg1_len >= seg2_len:
                mx, my = (pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2
            else:
                mx, my = (pts[1][0] + pts[2][0]) / 2, (pts[1][1] + pts[2][1]) / 2
            ax.text(mx, my, e["label"],
                    ha="center", va="center", fontsize=6.5, color=TEXT,
                    zorder=7,
                    bbox=dict(facecolor="white", edgecolor="#dddddd",
                              boxstyle="round,pad=0.25", alpha=0.96, linewidth=0.5))

    fmt = out_path.suffix.lstrip(".").lower()
    fig.savefig(out_path, format=fmt, dpi=160,
                bbox_inches=None, pad_inches=0, facecolor="white")
    plt.close(fig)


# ────────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────────
def main():
    excal = excalidraw()
    excal_path = OUT / "architecture.excalidraw"
    excal_path.write_text(json.dumps(excal, indent=2), encoding="utf-8")
    print(f"[ok] wrote {excal_path}  ({excal_path.stat().st_size:,} bytes, {len(excal['elements'])} elements)")

    drawio_xml = drawio()
    drawio_path = OUT / "architecture.drawio"
    drawio_path.write_text(drawio_xml, encoding="utf-8")
    print(f"[ok] wrote {drawio_path}  ({drawio_path.stat().st_size:,} bytes)")

    svg_path = OUT / "architecture.svg"
    matplotlib_render(svg_path)
    print(f"[ok] wrote {svg_path}  ({svg_path.stat().st_size:,} bytes)")

    png_path = OUT / "architecture.png"
    matplotlib_render(png_path)
    print(f"[ok] wrote {png_path}  ({png_path.stat().st_size:,} bytes)")

    # Light validation: both source files parse cleanly.
    json.loads(excal_path.read_text(encoding="utf-8"))
    import xml.etree.ElementTree as ET
    ET.fromstring(drawio_path.read_text(encoding="utf-8"))
    ET.fromstring(svg_path.read_text(encoding="utf-8"))
    print(f"[ok] all four files parse cleanly  (shapes={len(shapes)}, edges={len(edges)})")


if __name__ == "__main__":
    main()
