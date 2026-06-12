# ============================================================================
# push-bot.ps1 — VMSS in-place EchoBot.dll patch (dev iteration only)
# ----------------------------------------------------------------------------
# Hot-replace the bot's EchoBot.dll on a single VMSS instance without
# re-running install.ps1 (which is wedged behind the bootstrap CSE).
#
# This is for FAST DEV ITERATION ONLY. Production deploys MUST go through
# the agent-deploy.yml pipeline so the artifact is versioned, the
# blob/MD5 is auditable, and all instances stay in sync.
#
# Required env vars on the dev box:
#   $env:BOT_DLL_BLOB_URL  =
#     'https://<DEPLOY_STORAGE_ACCOUNT>.blob.core.windows.net/agent-artifacts/patches/EchoBot.dll'
#
# Required parameters when invoking remotely:
#   az vmss run-command invoke -g <RESOURCE_GROUP> -n <VMSS_NAME> --instance-id 0 `
#       --command-id RunPowerShellScript --scripts @scripts/vmss/push-bot.ps1
#
# Upload a fresh DLL with:
#   az storage blob upload --account-name <DEPLOY_STORAGE_ACCOUNT> `
#       --container-name agent-artifacts `
#       --name patches/EchoBot.dll --file _diag/publish/bot/EchoBot.dll `
#       --auth-mode login --overwrite true
# ============================================================================

$ErrorActionPreference = 'Stop'

$url = $env:BOT_DLL_BLOB_URL
if ([string]::IsNullOrWhiteSpace($url)) {
    throw "BOT_DLL_BLOB_URL env var is required. Set it on the VMSS instance before invoking this script."
}
$dst = 'C:\lisa\extracted\EchoBot\EchoBot.dll'

# Use the VMSS managed identity to download the blob (no SAS, no anonymous access).
$tokenResp = Invoke-RestMethod -Headers @{Metadata='true'} `
    -Uri 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/'
$tok = $tokenResp.access_token

Stop-Service lisa-bot -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Invoke-WebRequest -Uri $url `
    -Headers @{ 'Authorization' = "Bearer $tok"; 'x-ms-version' = '2021-08-06' } `
    -OutFile "$dst.new" -UseBasicParsing
$newSize = (Get-Item "$dst.new").Length
"Downloaded $newSize bytes -> $dst.new"
Move-Item -Force "$dst.new" $dst

Start-Service lisa-bot
Start-Sleep -Seconds 5

Get-Service lisa-bot,lisa-sidecar | Select-Object Status,Name | Out-String
'--- bot.err last 20 ---'
Get-Content C:\ProgramData\lisa\logs\lisa-bot.err.log -Tail 20 -ErrorAction SilentlyContinue
'--- bot.out last 40 (filtered) ---'
Get-Content C:\ProgramData\lisa\logs\lisa-bot.out.log -Tail 40 -ErrorAction SilentlyContinue |
    Select-String -Pattern 'sidecar|SpeechService|NotifyCallEstablished|call_established|ERROR|WARN' -CaseSensitive:$false
