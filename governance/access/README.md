# `governance/access/` — access governance (Conditional Access + entitlement gate)

How access to the avatar's identity and its sensitive data is governed. Two
complementary controls, both anchored in `governance/agent-registry.yaml`:

## 1. Conditional Access on the workload identity

[`conditional-access/ca-agents-baseline.json`](conditional-access/ca-agents-baseline.json)
is the Conditional Access policy applied to the **agent service principal** that
backs the avatar (`id-tva-lisa`). The registry references it per agent via
`access.conditional_access: ca-agents-baseline`.

- Ships as **report-only** (`enabledForReportingButNotEnforced`) — flip to
  `enabled` after you've validated it doesn't break legitimate calls.
- Replace `<sp-object-id:id-tva-lisa>` with the real service-principal object id
  (from the `agent365.bicep` identity outputs) and `<named-location-id:...>` with
  your trusted network location.
- The tenant-binding tool checks this placeholder is mapped:
  `python governance/apply_tenant_config.py --config <your>.local.yaml --check`
  fails if `conditional_access['ca-agents-baseline']` is still unset.

## 2. Sensitive-data entitlement gate (runtime)

The registry gates sensitive data scopes behind Entra groups
(`access.sensitive_data_groups`, e.g. `interview-transcript: [grp-tva-recruiters]`).
At runtime `agentgov.security.AgentGuard` enforces it: a user who is **not** in
the required group gets a hard **block** (not just redaction) on sensitive
findings in gated output.

The user's real group memberships are resolved from Microsoft Graph
(`agentgov.auth.resolve_group_ids` → `/me/memberOf`), so dropping a user from
`grp-tva-recruiters` immediately denies them the gated `interview-transcript`
scope — proven by
`agentgov/tests/test_entitlements.py::test_exit_criterion_removing_group_denies_access`.

## Provisioning order

See [`../../scripts/agent365/README.md`](../../scripts/agent365/README.md) for the
end-to-end admin runbook (Purview labels/DLP, audit routing, Defender, CA).
