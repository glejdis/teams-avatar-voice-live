# Deploy guide

A one-pager for the **production VMSS path**. For the local browser-WebRTC
fallback you can skip this entire doc and follow the
[browser-fallback README](../browser-fallback/README.md) instead.

End-to-end: ~1 hour of clicking + ~30 min of waiting for the first VMSS
rollout.

## Prerequisites

You need **all** of the following in the target Azure tenant:

1. **Azure subscription** with permission to create resource groups, AAD
   app registrations, and `Microsoft.Compute/virtualMachineScaleSets`.
2. **Foundry / Azure AI Services resource** in a region that supports Voice
   Live + avatar streaming (e.g. Sweden Central, West Europe).
3. **Microsoft 365 tenant** the same as #1, with at least one **licensed**
   user mailbox that will own the meetings.
4. **AAD bot app registration** — a separate AAD app with the
   Microsoft Graph `Calls.JoinGroupCall.All` + `Calls.AccessMedia.All`
   permissions, registered as a Teams calling bot. Documented end-to-end at
   [aka.ms/teams-calling-bot-prereqs](https://learn.microsoft.com/microsoftteams/platform/bots/calls-and-meetings/registering-calling-bot).
5. **Application Access Policy** in Teams scoped to the organiser mailbox
   from #3. *Can take up to 24 h to propagate* — the `launcher.graph_client`
   has a delegated-auth fallback that lets you start testing immediately.
6. **GitHub Actions OIDC** federation between this repo and the
   subscription. Bootstrap with
   `scripts/bootstrap-oidc.ps1` (one-time, see below).

## Step 1 — Bootstrap OIDC

```powershell
.\scripts\bootstrap-oidc.ps1 `
    -SubscriptionId <SUBSCRIPTION_ID> `
    -ResourceGroup <RESOURCE_GROUP> `
    -GitHubOrg <YOUR_GH_ORG> `
    -GitHubRepo teams-avatar-voice-live
```

This creates the AAD app + federated credential and prints the three GitHub
Actions secrets you need to set (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`). Set them under **Settings → Secrets and variables
→ Actions** in this repo.

## Step 2 — Fill in `bicepparam` files

```powershell
Copy-Item .\infra\params\dev.bicepparam.example .\infra\params\dev.bicepparam
# Edit the file — every <PLACEHOLDER> token must be replaced.
```

Required placeholders:

| Token | What it is |
|---|---|
| `<RESOURCE_GROUP>` | Target RG (will be created if missing) |
| `<LOCATION>` | Azure region (must match Foundry's region) |
| `<ACR_NAME>` | Globally-unique ACR name |
| `<VMSS_NAME>` | Globally-unique VMSS name |
| `<BOT_FQDN>` | DNS for the App Gateway frontend (TLS) |
| `<TENANT_ID>` / `<SUBSCRIPTION_ID>` | From your AAD tenant |
| `<ADMIN_EMAIL>` | Recipient for cost / health alerts |

## Step 3 — Deploy infrastructure

Trigger the `infra-deploy.yml` workflow from the Actions tab. It runs a
**what-if** first; review the planned resource set, then re-run with
`apply=true`. Expect ~15 min for the first apply (VMSS + App Gateway).

You can also run locally:

```powershell
az deployment sub create `
    --location <LOCATION> `
    --template-file .\infra\main.bicep `
    --parameters .\infra\params\dev.bicepparam `
    --what-if
```

The deployment outputs the names you need for step 4:
`vmssName`, `keyVaultName`, `artifactsContainer`, `acrName`,
`storageAccountName`.

## Step 4 — Push the Foundry agent + the bot

The single workflow `agent-deploy.yml` handles both, in parallel:

- **Hosted agent (Foundry)** — builds `hosted-agent/Dockerfile`, pushes to
  ACR, and updates the agent deployment in your Foundry project (using
  `hosted-agent/deploy.sh`).
- **VMSS bot + sidecar** — builds the C# bot from the `bot/` submodule
  (`bot/src/EchoBot/EchoBot.csproj`), zips it as `agent-<sha>.zip`, uploads
  to the storage account, then triggers a VMSS rolling update so each
  instance pulls the new zip via `scripts/vmss/install.ps1`.

Trigger by pushing to `main` (the workflow filters on `hosted-agent/**` and
`bot/**`), or run it manually from the Actions tab.

## Step 5 — Verify

```bash
python -m launcher schedule \
    --to your-own-email@yourdomain.com \
    --start +5 \
    --subject "Smoke test"
```

You should:
1. Receive the invite email within ~5 s.
2. Click the Teams link, join the meeting.
3. See the avatar (Lisa, by default) already in the meeting or in the
   lobby. Admit her.
4. Have a conversation — the persona's instructions live in
   `hosted-agent/personas/lisa.md`; edit and redeploy the hosted agent to
   change them.

If step 3 fails, see [RUNBOOK.md § Diagnosing common failures](RUNBOOK.md#4-diagnosing-common-failures).

## Step 6 — Schedule cost-control

Once the smoke test passes, set up the morning/evening start/stop loop from
[RUNBOOK.md § Stop the stack overnight](RUNBOOK.md#1-stop-the-stack-overnight-the-biggest-cost-lever).

## Reference

For details on each Bicep module (network, KV private endpoint, VMSS
bootstrap, App Gateway, monitor), see [`infra/README.md`](../infra/README.md).
