# ============================================================================
# push-sidecar.ps1 — VMSS in-place avatar-sidecar main.py patch (dev only)
# ----------------------------------------------------------------------------
# Hot-replace the sidecar's main.py on a single VMSS instance without
# re-running install.ps1. For FAST DEV ITERATION ONLY — production deploys
# MUST go through the agent-deploy.yml pipeline so the artifact is
# versioned and all instances stay in sync.
#
# Required env vars on the dev box:
#   $env:SIDECAR_PY_BLOB_URL  =
#     'https://<DEPLOY_STORAGE_ACCOUNT>.blob.core.windows.net/agent-artifacts/patches/sidecar-main.py'
#
# Upload a fresh main.py:
#   az storage blob upload --account-name <DEPLOY_STORAGE_ACCOUNT> `
#       --container-name agent-artifacts `
#       --name patches/sidecar-main.py --file bot/avatar-sidecar/main.py `
#       --auth-mode login --overwrite true
#
# Then invoke against the VMSS:
#   az vmss run-command invoke -g <RESOURCE_GROUP> -n <VMSS_NAME> --instance-id 0 `
#       --command-id RunPowerShellScript --scripts @scripts/vmss/push-sidecar.ps1
# Always redirect output to a file with an EXIT=$LASTEXITCODE sentinel;
# never cancel a sync invoke mid-flight (wedges the RunCommand extension).
# ============================================================================

$ErrorActionPreference = 'Stop'

$url = $env:SIDECAR_PY_BLOB_URL
if ([string]::IsNullOrWhiteSpace($url)) {
    throw "SIDECAR_PY_BLOB_URL env var is required. Set it on the VMSS instance before invoking this script."
}
$dest = 'C:\lisa\extracted\sidecar\main.py'
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null

# Use the VMSS managed identity to download the blob (no SAS, no anonymous access).
$tokenResp = Invoke-RestMethod -Headers @{Metadata='true'} `
    -Uri 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/'
$tok = $tokenResp.access_token

Stop-Service lisa-sidecar -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Invoke-WebRequest -Uri $url `
    -Headers @{ 'Authorization' = "Bearer $tok"; 'x-ms-version' = '2021-08-06' } `
    -OutFile "$dest.new" -UseBasicParsing
$newSize = (Get-Item "$dest.new").Length
"Downloaded $newSize bytes -> $dest.new"
Move-Item -Force "$dest.new" $dest

Start-Service lisa-sidecar
Start-Sleep -Seconds 5

Get-Service lisa-sidecar,lisa-bot | Select-Object Status,Name | Out-String
'--- sidecar.err last 20 ---'
Get-Content C:\ProgramData\lisa\logs\lisa-sidecar.err.log -Tail 20 -ErrorAction SilentlyContinue
'--- sidecar.out last 40 (filtered) ---'
Get-Content C:\ProgramData\lisa\logs\lisa-sidecar.out.log -Tail 40 -ErrorAction SilentlyContinue |
    Select-String -Pattern 'avatar|session|connected|disconnect|ERROR|WARN' -CaseSensitive:$false
