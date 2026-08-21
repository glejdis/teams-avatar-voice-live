# `governance/` — Agent governance for `teams-avatar-voice-live`

Makes the avatar a **first-class, governed agent identity** and enforces the
controls *as code* on every change and every turn. Ported from a production
Agent 365 governance program and adapted to this repo's single agent (Lisa, the
Voice Live avatar).

Three things this gives you:

1. **A registered agent identity** — `agent-registry.yaml` is the system of
   record (owner, purpose, data exposure, least-privilege scopes, backing Entra
   identity, lifecycle). It is the inventory an Agent 365 / Entra Agent ID
   surface consumes.
2. **Governance enforced in CI** — `validate_registry.py` turns the review
   checklist into executable rules; `generate_inventory.py --check` blocks
   drift between the registry and the committed dashboard.
3. **Governance enforced at runtime** — the [`agentgov`](../agentgov) package
   screens prompt-injection, applies DLP redaction, and emits an attributable
   `AGENT_AUDIT` event on every turn. It is wired into both transports:
   [`hosted-agent/`](../hosted-agent) (Agent Framework middleware) and
   [`browser-fallback/`](../browser-fallback) (transcript boundaries).

## Contents

| Path | Purpose |
| --- | --- |
| `agent-registry.yaml` | The governed agent identity (Lisa) — owner, data, least privilege, access gate, lifecycle |
| `validate_registry.py` | Pure-Python validator: governance policy as code (exit 0/1) |
| `generate_inventory.py` | Renders `inventory/agent-inventory.{html,json}` from the registry; `--check` is the CI drift gate |
| `security/dlp-policy.yaml` | DLP sensitive-info types + per-sensitivity actions + labels (read by `agentgov.security`) |
| `schema/agent_registry.schema.json` | JSON Schema for editor / docs support |
| `inventory/` | Generated dashboard (HTML) + machine inventory (JSON) |
| `tenant/` | Where real tenant ids (Entra groups, CA policy) bind to the placeholders before deploy |
| `tests/` | Unit tests for the validator + inventory generator |

## Governance rules enforced (checklist → executable policy)

| Control | Enforced rule (`validate_registry.py`) |
| --- | --- |
| Least privilege | `least_privilege.graph_scopes` rejects `.default`, `*`, `Directory.ReadWrite.All` |
| Accountability | Any agent with `data.personal_data: true` must declare an `owner` **and** `human_oversight` |
| Co-determination | Any agent with `data.employment_decision: true` must have `co_determination: obtained` + `human_oversight` |
| Identity integrity | `entra.identity_name` unique; `identity_type`/`status` from a fixed set |
| Data classification | `data.sensitivity` ∈ {public, internal, confidential, restricted} |
| Least-privilege roles | `least_privilege.azure_roles` must come from `defaults.azure_role_catalogue` |
| Sensitive-data gate | `special_category`/`restricted` data must be gated by an `access.sensitive_data_groups` Entra group |
| Lifecycle | `lifecycle` ∈ {proposed, active, retired} |

## Runtime enforcement (per turn)

```python
from agentgov.security import AgentGuard

guard = AgentGuard.from_registry("lisa-voice-avatar")

checked = guard.screen_input(user_text, identity, action="interview.turn")
if checked.blocked:                       # prompt-injection / jailbreak
    ...

out = guard.screen_output(answer, identity, action="interview.turn",
                          user_groups=user_groups)  # DLP + entitlement
```

Every call emits an attributable `AGENT_AUDIT` event keyed by
`(user oid, agent id, action)` carrying the sensitivity label, DLP verdict, and
any injection signals — the evidence a compliance reviewer needs, attributable
to a real person and a registered agent, never the app identity.

## Run locally

```bash
pip install pyyaml
python governance/validate_registry.py
python governance/generate_inventory.py          # regenerate the dashboard
python governance/generate_bicep_params.py        # regenerate infra params from the registry
python -m unittest discover -s governance/tests -p "test_*.py" -v
python -m unittest discover -s agentgov/tests   -p "test_*.py" -v
```

## Provisioning the agent identity (Phase 5)

The registry drives the infrastructure. `generate_bicep_params.py` emits
`infra/params/agent365.params.json` (drift-checked in CI), which
[`infra/agent365.bicep`](../infra/agent365.bicep) consumes to provision:

- one **User-Assigned Managed Identity** per agent (`id-tva-lisa`),
- its **least-privilege** Azure role assignments (only the registry
  `azure_roles`), and
- the **`AGENT_AUDIT` sink** — a Log Analytics `AgentAudit_CL` table + a Sentinel
  rule that alerts on blocked / injection / DLP-block decisions.

Bind real tenant ids (Entra group GUIDs, CA policy id, identity) with
`apply_tenant_config.py` — see [`tenant/README.md`](tenant/README.md). After
deploy, set `entra.status: provisioned` in the registry.

## Lifecycle: request → register → provision → retire

1. **Request** a new agent via the `agent-request` issue template.
2. **Register** it in `agent-registry.yaml` (`lifecycle: proposed`); the PR runs
   `governance-validate`.
3. **Provision** the backing identity + least-privilege roles, then set
   `entra.status: provisioned`, `lifecycle: active`.
4. **Retire** by setting `lifecycle: retired` and deprovisioning; the entry is
   kept for audit history.

## Mapping to Agent 365 / Entra Agent ID

Today the agent is backed by a **User-Assigned Managed Identity** (the concrete
Azure primitive). When the **Entra Agent ID** control plane is available, set
`entra.identity_type: entra-agent-id` — the registry shape already matches, so
owners, scopes, lifecycle, and the inventory carry over directly.
