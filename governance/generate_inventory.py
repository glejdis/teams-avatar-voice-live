"""Generate the Agent 365 governance inventory/dashboard from the registry.

Phase 4 (visualization & lifecycle): turns the single source of truth
(``governance/agent-registry.yaml`` + the access/security metadata from Phases
2–3) into one **live inventory** the CoE governs from — a self-contained HTML
dashboard plus a machine-readable JSON the Agent 365 visualization surface (or
any tooling) can consume.

Deterministic output (no embedded timestamps) so ``--check`` can detect drift in
CI: the committed artifacts must always match the registry.

Usage::

    python governance/generate_inventory.py            # (re)generate artifacts
    python governance/generate_inventory.py --check     # fail if artifacts are stale
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from validate_registry import DEFAULT_REGISTRY, load_registry, validate_registry  # noqa: E402

INVENTORY_DIR = _HERE / "inventory"
HTML_PATH = INVENTORY_DIR / "agent-inventory.html"
JSON_PATH = INVENTORY_DIR / "agent-inventory.json"

_BROAD_SCOPES = {".default", "*", "directory.readwrite.all"}

# Fallback sensitivity -> label (overridden by the DLP policy if present).
# Keep in sync with governance/security/dlp-policy.yaml `sensitivity_labels`.
_FALLBACK_LABELS = {
    "public": "Public",
    "internal": "Internal",
    "confidential": "Confidential",
    "restricted": "Highly Confidential",
}

# Governance posture checks shown per agent (id -> human label).
POSTURE_CHECKS = {
    "identity_registered": "Registered identity",
    "least_privilege": "Least privilege (no .default)",
    "human_oversight": "Human oversight",
    "conditional_access": "Conditional Access",
    "data_gated": "Sensitive data gated",
}


def _posture(agent: dict) -> dict:
    data = agent.get("data") or {}
    lp = agent.get("least_privilege") or {}
    entra = agent.get("entra") or {}
    access = agent.get("access") or {}
    sdg = access.get("sensitive_data_groups") or {}
    scopes = lp.get("graph_scopes") or []

    needs_gate = bool(data.get("special_category")) or data.get("sensitivity") == "restricted"
    return {
        "identity_registered": bool(entra.get("identity_name")),
        "least_privilege": all(str(s).strip().lower() not in _BROAD_SCOPES for s in scopes),
        "human_oversight": bool(str(agent.get("human_oversight") or "").strip()),
        "conditional_access": bool(access.get("conditional_access")),
        "data_gated": bool(sdg) or not needs_gate,
    }


def build_inventory(registry: dict, *, label_map: dict | None = None) -> dict:
    """Build the inventory model (pure — no file IO)."""
    labels = label_map or _FALLBACK_LABELS
    agents_out: list[dict] = []

    for agent in registry.get("agents", []) or []:
        data = agent.get("data") or {}
        lp = agent.get("least_privilege") or {}
        entra = agent.get("entra") or {}
        access = agent.get("access") or {}
        posture = _posture(agent)
        sensitivity = data.get("sensitivity")

        agents_out.append(
            {
                "id": agent.get("id", ""),
                "display_name": agent.get("display_name", ""),
                "app": agent.get("app", ""),
                "runtime": agent.get("runtime", ""),
                "owner": agent.get("owner", ""),
                "lifecycle": agent.get("lifecycle", ""),
                "identity": {
                    "name": entra.get("identity_name", ""),
                    "type": entra.get("identity_type", ""),
                    "status": entra.get("status", ""),
                },
                "data": {
                    "personal_data": bool(data.get("personal_data")),
                    "special_category": bool(data.get("special_category")),
                    "employment_decision": bool(data.get("employment_decision")),
                    "sensitivity": sensitivity or "",
                    "label": labels.get(sensitivity, "Unclassified"),
                },
                "least_privilege": {
                    "graph_scopes": list(lp.get("graph_scopes") or []),
                    "azure_roles": list(lp.get("azure_roles") or []),
                    "data_scopes": list(lp.get("data_scopes") or []),
                },
                "access": {
                    "conditional_access": access.get("conditional_access", ""),
                    "sensitive_data_groups": dict(access.get("sensitive_data_groups") or {}),
                },
                "sub_agents": list(agent.get("sub_agents") or []),
                "posture": posture,
                "posture_score": sum(1 for v in posture.values() if v),
                "posture_total": len(posture),
            }
        )

    lifecycles: dict[str, int] = {}
    for entry in agents_out:
        lifecycles[entry["lifecycle"]] = lifecycles.get(entry["lifecycle"], 0) + 1

    summary = {
        "total_agents": len(agents_out),
        "lifecycles": lifecycles,
        "personal_data_agents": sum(1 for a in agents_out if a["data"]["personal_data"]),
        "gated_agents": sum(1 for a in agents_out if a["access"]["sensitive_data_groups"]),
        "total_sub_agents": sum(len(a["sub_agents"]) for a in agents_out),
        "fully_compliant": sum(1 for a in agents_out if a["posture_score"] == a["posture_total"]),
    }
    return {"summary": summary, "agents": agents_out}


def render_json(inventory: dict) -> str:
    return json.dumps(inventory, indent=2, sort_keys=True) + "\n"


def _chip(text: str, kind: str = "") -> str:
    cls = f"chip {kind}".strip()
    return f'<span class="{cls}">{html.escape(str(text))}</span>'


def _posture_dots(posture: dict) -> str:
    dots = []
    for key, label in POSTURE_CHECKS.items():
        ok = posture.get(key)
        symbol = "✓" if ok else "✕"
        cls = "ok" if ok else "bad"
        dots.append(f'<span class="dot {cls}" title="{html.escape(label)}">{symbol}</span>')
    return "".join(dots)


def _agent_card(agent: dict) -> str:
    e = html.escape
    ident = agent["identity"]
    data = agent["data"]
    lp = agent["least_privilege"]
    access = agent["access"]

    scopes = ", ".join(lp["graph_scopes"]) or "—"
    roles = ", ".join(lp["azure_roles"]) or "—"
    gates = (
        "<br>".join(
            f"{e(scope)} → {e(', '.join(groups))}"
            for scope, groups in access["sensitive_data_groups"].items()
        )
        or "—"
    )
    flags = []
    if data["personal_data"]:
        flags.append(_chip("personal data", "amber"))
    if data["special_category"]:
        flags.append(_chip("special category", "red"))
    if data["employment_decision"]:
        flags.append(_chip("employment decision", "red"))
    flags_html = " ".join(flags) or "—"

    return f"""
    <article class="card">
      <header>
        <div>
          <h3>{e(agent['display_name'])}</h3>
          <code>{e(agent['id'])}</code>
        </div>
        <div class="posture">{_posture_dots(agent['posture'])}
          <span class="score">{agent['posture_score']}/{agent['posture_total']}</span>
        </div>
      </header>
      <dl>
        <dt>App</dt><dd>{e(agent['app'])} <span class="muted">({e(agent['runtime'])})</span></dd>
        <dt>Owner</dt><dd>{e(agent['owner'])}</dd>
        <dt>Lifecycle</dt><dd>{_chip(agent['lifecycle'], 'green' if agent['lifecycle']=='active' else '')}</dd>
        <dt>Identity</dt><dd><code>{e(ident['name'])}</code><br>
          <span class="muted">{e(ident['type'])} · {e(ident['status'])}</span></dd>
        <dt>Sensitivity</dt><dd>{_chip(data['label'], 'red' if data['sensitivity']=='restricted' else 'amber')}</dd>
        <dt>Data flags</dt><dd>{flags_html}</dd>
        <dt>Graph scopes</dt><dd>{e(scopes)}</dd>
        <dt>Azure roles</dt><dd>{e(roles)}</dd>
        <dt>Conditional Access</dt><dd>{e(access['conditional_access'] or '—')}</dd>
        <dt>Sensitive-data gates</dt><dd>{gates}</dd>
        <dt>Sub-agents</dt><dd>{e(str(len(agent['sub_agents'])))}{(' · ' + e(', '.join(agent['sub_agents']))) if agent['sub_agents'] else ''}</dd>
      </dl>
    </article>"""


def render_html(inventory: dict) -> str:
    s = inventory["summary"]
    cards = "\n".join(_agent_card(a) for a in inventory["agents"])
    lifecycles = " · ".join(f"{k}: {v}" for k, v in sorted(s["lifecycles"].items())) or "—"
    legend = " ".join(
        f'<span class="legend-item"><span class="dot ok">✓</span>{html.escape(v)}</span>'
        for v in POSTURE_CHECKS.values()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Teams Avatar Voice Live — Agent Governance Inventory</title>
<style>
  :root {{ --bg:#0f172a; --panel:#1e293b; --ink:#e2e8f0; --muted:#94a3b8;
           --line:#334155; --ok:#22c55e; --bad:#ef4444; --amber:#f59e0b; --accent:#38bdf8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--ink); }}
  .wrap {{ max-width:1200px; margin:0 auto; padding:24px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); margin:0 0 20px; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:20px; }}
  .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }}
  .stat b {{ display:block; font-size:26px; }}
  .stat span {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  .legend {{ color:var(--muted); font-size:12px; margin-bottom:16px; display:flex; flex-wrap:wrap; gap:14px; }}
  .legend-item {{ display:inline-flex; align-items:center; gap:5px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:16px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .card header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:8px; border-bottom:1px solid var(--line); padding-bottom:10px; margin-bottom:10px; }}
  .card h3 {{ margin:0 0 2px; font-size:16px; }}
  .card code {{ color:var(--accent); font-size:12px; }}
  .posture {{ display:flex; align-items:center; gap:4px; white-space:nowrap; }}
  .score {{ color:var(--muted); font-size:12px; margin-left:4px; }}
  .dot {{ display:inline-grid; place-items:center; width:18px; height:18px; border-radius:50%; font-size:11px; }}
  .dot.ok {{ background:rgba(34,197,94,.15); color:var(--ok); }}
  .dot.bad {{ background:rgba(239,68,68,.15); color:var(--bad); }}
  dl {{ display:grid; grid-template-columns:130px 1fr; gap:6px 12px; margin:0; }}
  dt {{ color:var(--muted); }}
  dd {{ margin:0; word-break:break-word; }}
  .muted {{ color:var(--muted); }}
  .chip {{ display:inline-block; padding:1px 8px; border-radius:999px; background:var(--line); font-size:12px; }}
  .chip.green {{ background:rgba(34,197,94,.18); color:var(--ok); }}
  .chip.amber {{ background:rgba(245,158,11,.18); color:var(--amber); }}
  .chip.red {{ background:rgba(239,68,68,.18); color:var(--bad); }}
  footer {{ color:var(--muted); font-size:12px; margin-top:24px; border-top:1px solid var(--line); padding-top:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Teams Avatar Voice Live — Agent Governance Inventory</h1>
  <p class="sub">Generated from <code>governance/agent-registry.yaml</code> · the live inventory the AI Center of Excellence governs from.</p>

  <section class="stats">
    <div class="stat"><b>{s['total_agents']}</b><span>Governed agents</span></div>
    <div class="stat"><b>{s['total_sub_agents']}</b><span>Specialist sub-agents</span></div>
    <div class="stat"><b>{s['personal_data_agents']}</b><span>Process personal data</span></div>
    <div class="stat"><b>{s['gated_agents']}</b><span>Sensitive-data gated</span></div>
    <div class="stat"><b>{s['fully_compliant']}/{s['total_agents']}</b><span>Full posture</span></div>
  </section>

  <p class="sub">Lifecycle: {lifecycles}</p>
  <div class="legend">{legend}</div>

  <div class="grid">
{cards}
  </div>

  <footer>
    Generated by <code>governance/generate_inventory.py</code> ·
    <span id="gen"></span> ·
    Maps to the Microsoft Agent 365 registry / visualization surface.
  </footer>
</div>
<script>document.getElementById('gen').textContent =
  'rendered ' + new Date().toISOString().slice(0,10);</script>
</body>
</html>
"""


def generate(registry_path: Path, label_map: dict | None = None) -> tuple[str, str]:
    data = load_registry(registry_path)
    result = validate_registry(data)
    if not result.ok:
        raise ValueError(
            "registry is invalid; fix governance violations before generating:\n  - "
            + "\n  - ".join(result.errors)
        )
    inventory = build_inventory(data, label_map=label_map)
    return render_html(inventory), render_json(inventory)


def _load_label_map() -> dict | None:
    try:
        repo_root = _HERE.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from agentgov.security.policy import load_policy_file  # type: ignore

        policy = load_policy_file()
        return dict(policy.sensitivity_labels)
    except Exception:
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate the agent governance inventory.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--check", action="store_true", help="fail if committed artifacts are stale")
    args = parser.parse_args(argv[1:])

    try:
        html_out, json_out = generate(Path(args.registry), _load_label_map())
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.check:
        stale = []
        for path, expected in ((HTML_PATH, html_out), (JSON_PATH, json_out)):
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            if actual != expected:
                stale.append(path.name)
        if stale:
            print(
                "FAILED: inventory artifacts are stale: "
                + ", ".join(stale)
                + "\n  Run: python governance/generate_inventory.py",
                file=sys.stderr,
            )
            return 1
        print("OK: inventory artifacts are up to date.")
        return 0

    INVENTORY_DIR.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html_out, encoding="utf-8")
    JSON_PATH.write_text(json_out, encoding="utf-8")
    print(f"Wrote {HTML_PATH.relative_to(_HERE.parent)} and {JSON_PATH.relative_to(_HERE.parent)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
