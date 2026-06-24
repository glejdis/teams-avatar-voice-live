# `governance/tenant/` — tenant binding (Phase 5)

The registry and policy ship with **placeholders** so the repo is safe to make
public and validates in CI without leaking tenant data:

| Placeholder (committed) | Real value (per tenant, NOT committed) |
| --- | --- |
| `grp-tva-recruiters` | the Entra ID **group object id** that may view interview transcripts |
| `ca-agents-baseline` | the **Conditional Access** policy id applied to the agent identity |
| `id-tva-lisa` | the **User-Assigned Managed Identity** (or Entra Agent ID) backing Lisa |

Binding the placeholders to real ids is an **admin** step run against your
tenant — nothing here changes a tenant on its own.

## How to bind

1. Copy the example and fill in your real ids (keep the copy out of git):

   ```bash
   cp governance/tenant/tenant-config.example.yaml governance/tenant/tenant-config.local.yaml
   # edit tenant-config.local.yaml with your Entra group GUIDs / CA policy id
   ```

2. Provision the backing identity + least-privilege role assignments
   (`infra/modules/rbac.bicep`) and grant the delegated Graph scopes from
   `agent-registry.yaml` to the app registration.

3. At runtime, resolve a signed-in user's group memberships (Graph
   `/me/memberOf`) and pass them to `AgentGuard.screen_output(..., user_groups=...)`
   so the entitlement gate decides who may see gated data. The committed group
   *name* and the real group *GUID* are matched case-insensitively, so you can
   either map names → GUIDs here or set the real GUID directly in the registry
   in your private fork.

## Exit criterion

Drop a user from `grp-tva-recruiters` → their next request for the gated
`interview-transcript` scope is denied (proven by
`agentgov/tests/test_entitlements.py::test_exit_criterion_removing_group_denies_access`).
