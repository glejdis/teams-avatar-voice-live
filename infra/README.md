# `infra/` — Infrastructure as Code

Bicep templates for the **teams-avatar-voice-live** Azure footprint:
Foundry project, ACR, Storage, ACS, Key Vault, VMSS for the Teams bot,
Bastion (break-glass), and Monitor / App Insights.

```
infra/
├─ main.bicep                       # top-level: shared resources + optional avatar stack
├─ main.json                        # ARM compile of main.bicep (committed for diffability)
├─ avatar-stack.bicep               # avatar VMSS + KV + Bastion + Monitor (+ optional AppGw)
├─ vmss-bootstrap-extension.bicep   # standalone CSE re-runner (rare; usually triggered via main)
├─ keyvault-private-endpoint.bicep  # standalone KV-PE deploy (for recovery scenarios)
├─ modules/                         # network / monitor / keyvault / vmss / bastion / rbac …
└─ params/
   ├─ dev.bicepparam.example        # template — copy to dev.bicepparam, fill in
   └─ prod.bicepparam.example       # template — copy to prod.bicepparam, fill in
```

## Architecture at a glance

| Layer | Resources |
|---|---|
| **Compute** | VMSS (Windows 2022) hosting the Teams Graph bot + the Voice Live sidecar; AppGw (optional, signaling only); Bastion (break-glass) |
| **Identity** | User-assigned Managed Identity attached to VMSS — pulls cert from KV, reads ACR, calls Foundry. GitHub Actions OIDC identity (created by `scripts/bootstrap-oidc.ps1`). |
| **Secrets** | Key Vault with private endpoint inside the VNet; bot AAD client secret + TLS cert live here |
| **AI** | Foundry account + project that hosts the agent container (built from `hosted-agent/`) |
| **Comms** | Azure Communication Services (Teams meeting bridge) |
| **Artifacts** | Storage Account (`agent-artifacts` container) holding the zip the VMSS bootstraps from |
| **Bootstrap** | Custom Script Extension runs `scripts/vmss/install.ps1` on first-boot — pulls artifact zip from blob, registers `lisa-bot` + `lisa-sidecar` Windows services via NSSM, env-vars from KV |

## Two ways to deploy

### 1) GitOps (recommended)

Push to `main` touches `infra/**`, the
[`infra-deploy`](../.github/workflows/infra-deploy.yml) workflow runs Bicep
`what-if`; manual dispatch with `env: prod` applies. Push to
`hosted-agent/**` or `bot/**` triggers the
[`agent-deploy`](../.github/workflows/agent-deploy.yml) workflow which
builds + registers a new Foundry agent version AND rebuilds + uploads the
VMSS artifact zip.

### 2) Local one-shot (first deploy / disaster recovery)

```pwsh
# Copy + fill placeholders.
Copy-Item infra/params/prod.bicepparam.example infra/params/prod.bicepparam
notepad   infra/params/prod.bicepparam

# Deploy.
az login
az deployment group create `
  -g $env:AZURE_RESOURCE_GROUP `
  -f infra/avatar-stack.bicep `
  -p infra/params/prod.bicepparam `
  -p adminPassword=$env:VMSS_ADMIN_PASSWORD
```

## Prerequisites (one-time, before first deploy)

1. **Subscription & resource group.**
2. **GitHub OIDC federated identity** — run `scripts/bootstrap-oidc.ps1`
   once per `(env, repo)` pair. This creates a UAMI with
   `Contributor` + `Microsoft.Authorization/roleAssignments/write` on the
   target RG and configures the federated credential.
3. **GitHub Environment** named `prod` with:
   - **Secrets:** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
     `AZURE_SUBSCRIPTION_ID`, `VMSS_ADMIN_PASSWORD`. `SUBMODULE_TOKEN`
     is **optional** — only set it if you've forked `bot/` to a private
     repo. The default `bot/` submodule (`glejdis/my-echobot-repo`) is
     public; for that case, edit `agent-deploy.yml` to set
     `submodules: true` and drop the `SUBMODULE_TOKEN` checkout step.
   - **Variables:** `AZURE_RESOURCE_GROUP`, `FOUNDRY_ACCOUNT_NAME`,
     `FOUNDRY_PROJECT_NAME`, `INFRA_DEPLOYMENT_NAME` (optional, defaults
     to `main`).
4. **Tenant prereqs** — run `scripts/setup-teams-integration.ps1` once
   per tenant. This creates the bot AAD app, grants Graph
   `OnlineMeetings.ReadWrite.All` + `Mail.Send`, creates a Teams
   Application Access Policy, and grants the policy to the organising
   user.

## Day-2 ops

| Task | Where |
|---|---|
| Daily cost shutdown (Foundry public-access toggle + VMSS deallocate) | `scripts/ops/avatar-cost-control.ps1` |
| Hot-patch the bot DLL on a running VMSS instance | `scripts/vmss/push-bot.ps1` |
| Hot-patch the sidecar `main.py` on a running VMSS instance | `scripts/vmss/push-sidecar.ps1` |
| Rotate Key Vault secrets / re-issue cert | `.github/workflows/secret-rotation.yml` |
| Re-run CSE bootstrap on existing VMSS instances | Bump `cseRevision` in your `*.bicepparam` and redeploy |

## Recover individual components

The "Two ways to deploy" recipes above recreate the **entire** avatar stack
(network, KV, Monitor, VMSS, Bastion, optional AppGw — everything). For
one-off ops sessions or cost-driven cleanup where you only need to bring a
single component back, prefer a targeted command — otherwise you'll
accidentally restart deallocated VMSS instances or recreate AppGw too.

### Bastion (break-glass RDP/SSH)

Bastion Basic is ~USD 140/mo idle and there is no stop/pause — delete it
when you're done. The AzureBastionSubnet stays in `<NAME_PREFIX>-vnet`,
so recreating the bastion + its PIP is two commands and takes ~10
minutes:

```pwsh
$RG     = $env:AZURE_RESOURCE_GROUP
$PREFIX = "<NAME_PREFIX>"        # the namePrefix from your *.bicepparam, e.g. "myorg-lisa"
$LOC    = "swedencentral"

az network public-ip create -g $RG -n "$PREFIX-bastion-pip" `
  --sku Standard --allocation-method Static --location $LOC

az network bastion create -g $RG -n "$PREFIX-bastion" `
  --vnet-name "$PREFIX-vnet" --public-ip-address "$PREFIX-bastion-pip" `
  --sku Basic --location $LOC
```

When the session is done, drop both again — they survive a hard tab close
and keep charging:

```pwsh
az network bastion delete   -g $RG -n "$PREFIX-bastion"     --yes
az network public-ip delete -g $RG -n "$PREFIX-bastion-pip"
```

### VMSS instances (stop billing without losing config)

If you're iterating on the browser-fallback transport (or otherwise don't
need the Graph bot running), deallocate the instances instead of deleting
the VMSS — the config + bootstrap state + custom script extension stay,
so a later `start` brings the bot back identically:

```pwsh
$RG     = $env:AZURE_RESOURCE_GROUP
$VMSS   = "<VMSS_NAME>"          # the VMSS name from your deploy, e.g. "myorglisa"

az vmss deallocate -g $RG -n $VMSS   # stops compute billing on all instances
az vmss start      -g $RG -n $VMSS   # bring them back when needed
```

Two `Standard_D4s_v5` instances in Sweden Central cost roughly USD 290/mo
running; deallocated they cost only the OS disks (~USD 5/mo each).

### Public IP for AppGw / load balancer

If you delete the AppGw or load balancer but leave their public IPs
behind, the Standard PIPs keep charging ~USD 3.65/mo each. After deleting
the parent resource, also delete any orphaned PIPs:

```pwsh
az network public-ip list -g $RG `
  --query "[?ipConfiguration==null].name" -o tsv | `
  ForEach-Object { az network public-ip delete -g $RG -n $_ }
```

## Notes

- `main.bicep` is the **shared** infra (Foundry, ACR, ACS, Storage).
  `avatar-stack.bicep` is the bot-specific infra (VMSS, KV, Bastion).
  Setting `deployAvatarStack=false` in `main.bicep` lets you iterate on
  the Foundry side without rebuilding the VMSS, which is the dominant
  cost driver.
- AppGw is **optional** and signaling-only. Skip it unless you need
  per-instance round-robin routing across multiple VMSS instances.
- Bastion is **off by default** — toggle on only when you need a
  break-glass console session, then delete it again. Bastion is one of
  the most expensive idle resources in the stack.
- The persona / agent name (`lisa`, avatar character `lisa`) is
  the **shipped example**. The infra `agentName` default + the
  `hosted-agent/deploy.sh` `AGENT_NAME` default must match — both are
  `lisa`. If you swap the persona to something else, change both. See
  `hosted-agent/personas/README.md`. Internal infra names that contain
  `lisa` (NSSM service names, on-disk paths under `C:\lisa\`) are
  intentional — they follow the agent name and don't affect anything
  user-facing.
