# Runbook

Day-to-day operations for a deployed `teams_avatar_voice_live` stack. Assumes
you've already followed [DEPLOY.md](DEPLOY.md) and have a working VMSS +
Foundry agent.

## 1. Stop the stack overnight (the biggest cost lever)

The VMSS instance + the Speech "Standard Streaming Minute" meter run 24/7 if
left alone. For a demo/dev stack you only use during work hours, deallocating
the VMSS outside business hours typically saves **~70 %** of the avatar bill.

`scripts/ops/avatar-cost-control.ps1` wraps the routine:

```powershell
# Tonight (and every night you're done):
.\scripts\ops\avatar-cost-control.ps1 -Action Shutdown
# Then close any browser tabs running the browser-fallback / Speech Studio.
# 10 min later, confirm:
.\scripts\ops\avatar-cost-control.ps1 -Action CostCheck

# Tomorrow morning:
.\scripts\ops\avatar-cost-control.ps1 -Action Startup
# Wait ~3 min before placing a test call — NSSM services need to come up.
```

You can wrap the morning/evening commands as Windows Scheduled Tasks
(`08:00` start, `19:00` stop).

### Why streaming-minute is the meter to watch

The Speech avatar "Standard Streaming Minute" meter charges per minute of
active outbound video, not per minute of conversation. A browser tab left
open on the operator UI keeps the meter running even when no one is in the
meeting. The cost-control script's `CostCheck` action queries Cost
Management for today's spend on that meter — if the number is climbing
after `Shutdown`, you have a tab open somewhere.

## 2. Run the demo end-to-end

### Production path (VMSS Graph bot)

```bash
# 1. Make sure the stack is up.
.\scripts\ops\avatar-cost-control.ps1 -Action Startup

# 2. Schedule a meeting + dispatch the bot.
python -m launcher schedule \
    --to your.invitee@example.com \
    --start +5 \
    --duration-mins 30 \
    --subject "Demo: Avatar Interview" \
    --mode graph_bot

# 3. Invitee receives the email and clicks the Join Teams link.
#    The avatar is already in the meeting (it was POSTed to BOT_JOIN_ENDPOINT
#    in step 2). Admit it from the Teams lobby if your meeting policy requires.
```

### Local fallback path (browser WebRTC)

Use this when the VMSS isn't ready, or for laptop demos with no inbound
public endpoint:

```powershell
# Terminal 1 — the browser-fallback operator UI on port 3000.
cd .\browser-fallback
Copy-Item .env.example .env -ErrorAction SilentlyContinue
# Edit .env (Voice Live endpoint + ACS connection string + Foundry agent name).
python app.py            # serves http://localhost:3000

# Terminal 2 — schedule the meeting (uses browser_webrtc mode).
python -m launcher schedule \
    --to your.invitee@example.com \
    --start +5 \
    --mode browser_webrtc
```

The CLI writes `browser-fallback/data/latest-invite.json`. The operator page
polls that file and auto-fills the meeting URL. Click **Join Teams** to
connect ACS to the meeting; you (the operator) admit yourself from a Teams
client.

## 3. Pre-flight cleanup (before every local demo)

The #1 cause of "stuck Admitting", "port already in use", and "two avatars
in the lobby" is leftover Python processes from a prior run holding ports
3000 / 5001 / 5055. Run this from the repo root before starting:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -match 'teams_avatar_voice_live' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

foreach ($port in 3000, 8080) {
  $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($listener) { "STILL LISTENING port=$port pid=$($listener.OwningProcess)" }
  else { "FREE port=$port" }
}
```

Also close every browser tab pointing at the operator UI before starting.
You want exactly **one** operator tab open per demo.

## 4. Diagnosing common failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `python -m launcher schedule` exits with `Tenant provided in token does not match resource` | Local Azure CLI is signed into the wrong tenant | `az login --tenant <tenant-id>` |
| Email never arrives, no error | `GRAPH_CLIENT_SECRET` expired | Rotate in Key Vault → re-run `secret-rotation.yml` |
| `Avatar bot endpoint unreachable` | VMSS deallocated, or App Gateway probe failing | `Startup` script; then `az vmss list-instance-public-ips` to confirm health |
| Teams shows two avatars in lobby | Duplicate operator tab, or stale Python on port 3000 | Run the pre-flight cleanup block; restart on port 3000 only |
| Operator log says `getMediaStream is unavailable` | Patched ACS browser SDK bundle missing | `cd browser-fallback/rebuild-acs && python build_acs.py --version 1.42.1` |
| Lisa joins but no audio out | Sidecar can't reach Voice Live | Check `AGENT_AZURE_OPENAI_API_VERSION` matches your Foundry deployment |

## 5. Logs

| Surface | Where |
|---|---|
| `launcher` CLI | stdout; raise verbosity with `-v` |
| `hosted-agent` container | Foundry / Container Apps log stream |
| VMSS bot + sidecar | NSSM service stdout, mirrored to Log Analytics (see `infra/modules/monitor.bicep`) |
| Browser fallback | `browser-fallback/data/operator-events.jsonl` + the **Download Operator Log** button on the UI |
| Auto-join decisions | `browser-fallback/data/auto-join-decisions.jsonl` |

## 6. Security hygiene

- All secrets live in Key Vault; the only thing in env vars at runtime is the
  Key Vault URL + a managed-identity client ID.
- `secret-rotation.yml` runs on cron and rotates the bot's `X-Bot-Auth`
  shared secret + the Graph client secret. Trigger it manually after any
  suspected leak.
- Application Access Policy for the bot AAD app is scoped to a single
  organiser mailbox by default — widen only if you intentionally want the
  bot able to join meetings owned by any user in the tenant.

## 7. Cost telemetry & the costboard dashboard

Every transport meters a call the same way through `core/cost.py` and can
persist one **`CostRecord`** per call to an **Azure Table** (`callcosts`). The
**`costboard/`** app reads that table and shows totals, a per-component /
per-transport / per-persona breakdown, a runs table, and CSV export.

```
browser-fallback ─┐
VMSS sidecar ──────┼─▶ core.cost.CostSink ─▶ Azure Table (callcosts) ─▶ costboard
hosted-agent (opt) ┘
```

### 7.1 Turn on persistence

Persistence is **opt-in** — with nothing configured, the live in-call cost
panel still works and no rows are written. Set these (same vars drive the
writer and the dashboard; auth precedence: connection string → key → AAD):

| Var | Meaning |
|---|---|
| `COST_STORE_ACCOUNT` | Storage account name → `https://<acct>.table.core.windows.net` |
| `COST_STORE_ENDPOINT` | Explicit table endpoint (overrides `ACCOUNT`) |
| `COST_STORE_TABLE` | Table name (default `callcosts`) |
| `COST_STORE_CONNECTION_STRING` | Full connection string (overrides AAD) |
| `COST_STORE_KEY` | Account key (used with `ACCOUNT`/`ENDPOINT`) |
| `COST_STORE_KIND` | Force `none` to disable even when an account is set |

For Managed Identity / `az login`, the identity needs **Storage Table Data
Contributor** on the account. The infra Bicep (`infra/main.bicep`) creates the
table and grants that role to every principalId in **`costStorePrincipalIds`**
(plus `deployerPrincipalId` automatically). Add the costboard identity, the
VMSS user-assigned identity, and/or your own objectId there.

### 7.2 Run the dashboard (local)

```powershell
cd costboard
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
# point it at the table (or reuse the repo-root .env):
$env:COST_STORE_ACCOUNT = '<storage-account>'
.\.venv\Scripts\python app.py        # http://localhost:3100
```

Filters: month (`YYYY-MM`), transport, channel, persona, date range. CSV export
respects the active filters. `GET /healthz` reports whether the store is wired.

### 7.3 Wire the VMSS sidecar

The VMSS sidecar (`bot/avatar-sidecar/`) **implements** the emitter. Because
the sidecar deploys self-contained (the VMSS hot-patch path ships only
`main.py` + sibling modules and cannot import the parent `core` package), it
uses a **vendored copy** of the cost module at
`bot/avatar-sidecar/cost_telemetry.py`. That file is a verbatim copy of
`core/cost.py` — **keep the two in sync** whenever rates, the record schema, or
the sink change.

How it works at runtime:

1. `scripts/vmss/install.ps1` forwards `COST_STORE_ACCOUNT` / `COST_STORE_TABLE`
   into the sidecar service environment when you pass `-CostStoreAccount`
   (wired through `infra/vmss-bootstrap-extension.bicep`). The VM also sets
   `USE_MANAGED_IDENTITY=1` + `AZURE_TENANT_ID`, so the sink authenticates as
   the **VMSS system-assigned managed identity**.
2. On `/stream` connect, the sidecar starts a `CostMeter(is_teams=True)`; on
   each `response.done` it folds in the Voice Live token usage.
3. On disconnect it marks the meter ended, builds a `CostRecord`
   (`transport="vmss"`, ACS rate forced to 0 — the Graph bot path has no ACS
   leg) and `await _COST_SINK.write(record)`.

The whole path is **fail-soft and opt-in**: with no `COST_STORE_*` set, or if
`azure-data-tables` is missing, the sidecar runs normally and writes no rows.
The VMSS MI needs **Storage Table Data Contributor** on the account (granted
via `costStorePrincipalIds` in `infra/main.bicep`). See
[`bot/avatar-sidecar/README.md`](../bot/avatar-sidecar/README.md#cost-telemetry)
for the sidecar-side detail.

> **Rollout note:** updating the live VMSS sidecar happens through the
> `agent-deploy` pipeline / `scripts/vmss/push-sidecar.ps1`; pushing
> `cost_telemetry.py` alongside `main.py`. Do **not** redeploy
> `vmss-bootstrap-extension.bicep` against a running VMSS just to add the env
> vars — that reruns the custom-script extension.

### 7.4 What the numbers mean

Rates live in `core.cost.cost_rates()` and are `COST_*`-overridable. Voice Live
Pro audio/text and ACS are list-price-accurate; the **avatar per-minute rate is
an estimate** (Azure renders it dynamically) and is usually the dominant line
for Teams calls — set `COST_AVATAR_PER_MIN` to your invoiced rate for exact
figures.

