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
| **Meeting created + invite emailed, but no avatar ever joins** (bot log shows zero join requests) | `TEAMS_JOIN_MODE=browser_webrtc` → the dispatcher records the invite but **never asks the bot to join**; it expects a browser/WebRTC client that wasn't running | Use `TEAMS_JOIN_MODE=graph_bot` for hands-off joining (see below). The dispatcher now logs a loud `AVATAR JOIN DEFERRED TO BROWSER` warning + an `avatar.join.deferred` AGENT_AUDIT event for exactly this case |
| `python -m launcher schedule` exits with `Tenant provided in token does not match resource` | Local Azure CLI is signed into the wrong tenant | `az login --tenant <tenant-id>` |
| Email never arrives, no error | `GRAPH_CLIENT_SECRET` expired | Rotate in Key Vault → re-run `secret-rotation.yml` |
| `Avatar bot endpoint unreachable` | VMSS deallocated, or App Gateway probe failing | `Startup` script; then `az vmss list-instance-public-ips` to confirm health |
| Teams shows two avatars in lobby | Duplicate operator tab, or stale Python on port 3000 | Run the pre-flight cleanup block; restart on port 3000 only |
| Operator log says `getMediaStream is unavailable` | Patched ACS browser SDK bundle missing | `cd browser-fallback/rebuild-acs && python build_acs.py --version 1.42.1` |
| Lisa joins but no audio out | Sidecar can't reach Voice Live | Check `AGENT_AZURE_OPENAI_API_VERSION` matches your Foundry deployment |

### Which join mode? (`TEAMS_JOIN_MODE`)

`launcher.bot_dispatcher.dispatch` forks on the mode and reports `avatar_will_join`
in its result; whenever no avatar will actually enter, it logs a loud warning and
emits an `avatar.join.<outcome>` AGENT_AUDIT event (queryable in `AgentAudit_CL`
by `action_s`), so "invite emailed, nobody joined" is never silent.

| | `graph_bot` (recommended) | `browser_webrtc` |
|---|---|---|
| **Who joins** | The VMSS Lisa bot dials in automatically (`POST BOT_JOIN_ENDPOINT/joinCall`) | A browser/WebRTC client — only if an operator page or auto-join tab is **live** |
| **Hands-off?** | ✅ yes | ❌ no — needs a live browser |
| **Audit on dispatch** | `avatar.join.requested` (or `avatar.join.failed`) | `avatar.join.deferred` |
| **Use for** | Production, scheduled/unattended interviews | Local laptop demos |

For production, set `TEAMS_JOIN_MODE=graph_bot`, `BOT_JOIN_ENDPOINT=…`, and
`BOT_JOIN_REQUIRED=true` so a bot that can't join **fails loudly** instead of
emailing a link no avatar joins.


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
