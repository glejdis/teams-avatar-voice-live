# `scripts/agent365/` — tenant provisioning runbook (governance Phase 5)

These scripts **codify** the live Azure / M365 / Purview / Defender configuration
the governance framework needs. They are runbooks to be executed by an admin with
the right roles — **nothing changes a live tenant unless you run it**.

| # | Script | Does | Requires |
| --- | --- | --- | --- |
| 1 | `01-purview-labels-dlp.ps1` | Creates Purview sensitivity labels + a DLP policy mirroring `governance/security/dlp-policy.yaml` | Compliance Administrator |
| 2 | `02-defender-enable.ps1` | Wires `AGENT_AUDIT` → App Insights → Log Analytics and prints Defender enablement steps | Security Administrator |

## Companion IaC (deploy first, via the OIDC pipeline)

| Bicep | Provisions |
| --- | --- |
| `infra/agent365.bicep` | Agent identity (`agent-identities.bicep`) + least-privilege RBAC (`agent-rbac.bicep`) + the audit sink, driven by `infra/params/agent365.params.json` |
| `infra/modules/audit-sink.bicep` | Log Analytics `AgentAudit_CL` table + Microsoft Sentinel + a blocked-action analytics rule |

`agent365.params.json` is generated from the registry — never hand-edit:

```powershell
python governance/generate_bicep_params.py          # regenerate
python governance/generate_bicep_params.py --check    # CI guard (also covered by tests)
```

## Typical end-to-end order

```text
1. python governance/generate_bicep_params.py
2. az deployment group create -g <rg> -f infra/agent365.bicep `
     -p @infra/params/agent365.params.json `
     -p foundryAccountName=<f> workspaceName=<la> deployStorageAccountName=<s> keyVaultName=<kv>
3. scripts/agent365/01-purview-labels-dlp.ps1
4. scripts/agent365/02-defender-enable.ps1 -ResourceGroup <rg> -WorkspaceName <la>
   # -> copy APPLICATIONINSIGHTS_CONNECTION_STRING onto the hosted-agent + browser-fallback
5. Apply the Conditional Access policy:
     governance/access/conditional-access/ca-agents-baseline.json
     (report-only first; replace the <sp-object-id:...> / <named-location-id:...> placeholders)
6. Fill governance/tenant/tenant-config.local.yaml, then:
     python governance/apply_tenant_config.py --config governance/tenant/tenant-config.local.yaml --check
     python governance/apply_tenant_config.py --config governance/tenant/tenant-config.local.yaml
7. Set entra.status: provisioned / lifecycle: active in governance/agent-registry.yaml.
```

## Reality notes

- Entra **Agent ID** provisioning is preview; the avatar is backed by a
  User-Assigned Managed Identity today (`entra.identity_type` flips later).
- Purview custom regex info types (e.g. the German social-insurance number) need
  a custom Sensitive Information Type rule package — marked TODO in script 1.
- Defender "for agents" is preview and largely portal-driven; script 2 guides it.
