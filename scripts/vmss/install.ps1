# ─────────────────────────────────────────────────────────────────────────────
# install.ps1 — VMSS Custom Script Extension bootstrap
#
# Runs on every VMSS instance on first-boot (and on rolling-upgrade reimages).
# Idempotent. Fails fast on any error.
#
# Args (positional, passed by the CSE commandToExecute in vmss.bicep):
#   $1  bootstrapZipUrl              Storage URL to the artifact zip
#                                    (bot binaries + sidecar)
#   $2  keyVaultName                 KV that holds bot-aad-secret
#   $3  tlsCertSecretName            Name of the bot TLS PFX in KV (synced to
#                                    LocalMachine\My by the AKV VM extension)
#   $4  appInsightsConnectionString  Pass-through to bot+sidecar processes
#   $5  serviceFqdn                  Public DNS name the bot exposes (LB FQDN).
#                                    Empty string => fall back to the per-instance
#                                    public DNS (legacy / no-LB mode).
#   $6  mediaNatStartPort            First port of the LB media NAT pool. The bot's
#                                    InstancePublicPort = mediaNatStartPort + instanceId.
#                                    Default 8445.
#   $7  voiceLiveEndpoint            Voice Live / Foundry endpoint for sidecar.
#   $8  agentName                    Hosted Foundry agent name.
#   $9  agentVersion                 Hosted Foundry agent version. Empty => latest.
#   $10 foundryProjectName           Foundry project name.
#   $11 azureTenantId                Tenant ID used for token pinning.
#   $12 voiceLiveApiVersion          Voice Live API version.
#   $13 voiceLiveVoice               Azure voice name.
#   $14 avatarCharacter              Avatar character.
#   $15 avatarStyle                  Avatar style.
#   $16 agentLanguage                Sidecar language hint (passed to the bot
#                                    as LISA_LANG, which is the bot-side env
#                                    var contract — do not rename here).
#   $17 avatarBackgroundImageUrl     Optional public HTTPS URL Voice Live can
#                                    fetch to use as the avatar background.
#                                    Empty string allowed.
#   $18 avatarBackgroundColor        Optional hex color (e.g. "#00ff00ff")
#                                    used when no image URL is set.
#                                    Empty string allowed.
#
# Required artifact zip layout:
#   /EchoBot/         (published .NET 8 self-contained — EchoBot.exe + deps)
#   /sidecar/         (avatar-sidecar Python source + requirements.txt)
#   /tools/nssm.exe   (Non-Sucking Service Manager)
#   /tools/python-3.12.x-amd64.exe   (offline Python installer)
#   /tools/ffmpeg/    (ffmpeg binaries; bin/ on PATH)
#
# All non-secret config goes into machine env vars below. Secret config is
# read from KV at runtime by the install step (and re-read on rotation).
# ─────────────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string] $BootstrapZipUrl,
    [Parameter(Mandatory=$true)] [string] $KeyVaultName,
    [Parameter(Mandatory=$true)] [string] $TlsCertSecretName,
    [Parameter(Mandatory=$false)][string] $AppInsightsConnectionString = '',
    [Parameter(Mandatory=$false)][string] $ServiceFqdn = '',
    [Parameter(Mandatory=$false)][int]    $MediaNatStartPort = 8445,
    [Parameter(Mandatory=$false)][string] $VoiceLiveEndpoint = '',
    [Parameter(Mandatory=$false)][string] $LisaFoundryAgentName = '',
    [Parameter(Mandatory=$false)][string] $LisaFoundryAgentVersion = '',
    [Parameter(Mandatory=$false)][string] $LisaFoundryProjectName = '',
    [Parameter(Mandatory=$false)][string] $AzureTenantId = '',
    [Parameter(Mandatory=$false)][string] $VoiceLiveApiVersion = '2026-01-01-preview',
    [Parameter(Mandatory=$false)][string] $VoiceLiveVoice = 'en-US-AvaMultilingualNeural',
    [Parameter(Mandatory=$false)][string] $AvatarCharacter = 'lisa',
    [Parameter(Mandatory=$false)][string] $AvatarStyle = 'casual-sitting',
    [Parameter(Mandatory=$false)][string] $LisaLang = 'en',
    [Parameter(Mandatory=$false)][string] $AvatarBackgroundImageUrl = '',
    [Parameter(Mandatory=$false)][string] $AvatarBackgroundColor = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference     = 'SilentlyContinue'

$logRoot = 'C:\ProgramData\lisa\logs'
$appRoot = 'C:\lisa'
$sidecarJobMemoryLimitBytes = 1610612736
$null = New-Item -ItemType Directory -Path $logRoot -Force
$null = New-Item -ItemType Directory -Path $appRoot -Force

Start-Transcript -Path (Join-Path $logRoot 'install.log') -Append

function Write-Step([string]$msg) { Write-Host "[install] $msg" }

function Normalize-VoiceLiveEndpoint([string]$Endpoint) {
    $value = ''
    if ($null -ne $Endpoint) { $value = $Endpoint.Trim() }
    if ([string]::IsNullOrWhiteSpace($value)) { return '' }
    if ($value -notmatch '^https://') { throw "VoiceLiveEndpoint must be an https URL." }
    if ($value -match '<your-foundry-resource>') { throw "VoiceLiveEndpoint is still the placeholder value." }
    if ($value -match '\.cognitiveservices\.azure\.com/?$') {
        $value = $value -replace '\.cognitiveservices\.azure\.com/?$', '.services.ai.azure.com/'
    }
    if (-not $value.EndsWith('/')) { $value = "$value/" }
    return $value
}

function Get-UriHostSafe([string]$Endpoint) {
    try { return ([Uri]$Endpoint).Host } catch { return '' }
}

# ── 1) Download artifact zip via VMSS MI (Storage Blob Data Reader) ─────────
Write-Step "Downloading artifact: $BootstrapZipUrl"
$zip = Join-Path $env:TEMP 'lisa-artifact.zip'
$tokenResp = Invoke-RestMethod -Method Get -Headers @{ Metadata='true' } `
    -Uri 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/'
$blobToken = $tokenResp.access_token
Invoke-WebRequest -Uri $BootstrapZipUrl -OutFile $zip `
    -Headers @{ Authorization = "Bearer $blobToken"; 'x-ms-version' = '2021-12-02' } -UseBasicParsing

Write-Step "Extracting to $appRoot"
# Stop any running bot/sidecar services so we can overwrite their binaries
# (clrjit.dll, EchoBot.exe, sidecar venv DLLs are file-locked while running).
# This script is idempotent — services will be (re-)registered + started in
# section 9 below.
foreach ($svc in @('lisa-bot','lisa-sidecar')) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($s -and $s.Status -ne 'Stopped') {
        Write-Step "Stopping $svc to release file locks"
        Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
        # Wait briefly for the process to exit and release file handles.
        for ($i = 0; $i -lt 20 -and (Get-Service $svc).Status -ne 'Stopped'; $i++) { Start-Sleep -Milliseconds 500 }
    }
}
if (Test-Path "$appRoot\extracted") { Remove-Item "$appRoot\extracted" -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath "$appRoot\extracted" -Force

# ── 2) Install Python (offline) ─────────────────────────────────────────────
# CSE runs as SYSTEM in a process whose PATH is captured at parent-spawn time;
# updates to Machine PATH (via the Python installer) are NOT visible inside
# this script even after a refresh. We therefore resolve $pythonExe by
# probing the well-known install location instead of relying on PATH.
#
# We have observed Python's `/quiet` MSI bundle silently leaving an
# *incomplete* install (python.exe present, but `Lib\` empty → at runtime
# `ModuleNotFoundError: No module named 'encodings'`). Detect this and run
# the installer (in repair/reinstall mode) until the stdlib is intact.
function Test-PythonInstall([string]$exe) {
    if (-not (Test-Path $exe)) { return $false }
    $pyHome = Split-Path $exe -Parent
    return (Test-Path (Join-Path $pyHome 'Lib\encodings\__init__.py'))
}
function Install-Python([string]$installer, [string]$logPath) {
    $args = @('/quiet','InstallAllUsers=1','PrependPath=1',
              'Include_test=0','Include_pip=1','Include_doc=0','Include_launcher=0',
              "/log",$logPath)
    $p = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
    return $p.ExitCode
}
$pythonExe = $null
$pyCandidates = @(
    'C:\Program Files\Python312\python.exe',
    'C:\Program Files\Python311\python.exe',
    'C:\Program Files\Python310\python.exe'
)
foreach ($c in $pyCandidates) { if (Test-PythonInstall $c) { $pythonExe = $c; break } }
if (-not $pythonExe) {
    Write-Step 'Installing Python 3.12 (clean)'
    $pyInstaller = Get-ChildItem "$appRoot\extracted\tools\python-3.12*.exe" | Select-Object -First 1
    if (-not $pyInstaller) { throw 'Python installer missing in artifact' }
    # If a half-installed python is sitting at the target dir, uninstall first
    # so we get a clean repair on the second pass instead of a no-op.
    if (Test-Path 'C:\Program Files\Python312\python.exe') {
        Write-Step 'Detected incomplete Python install — uninstalling before retry'
        Start-Process -FilePath $pyInstaller.FullName -ArgumentList @('/uninstall','/quiet') -Wait | Out-Null
        Remove-Item 'C:\Program Files\Python312' -Recurse -Force -ErrorAction SilentlyContinue
    }
    $rc = Install-Python $pyInstaller.FullName (Join-Path $logRoot 'python-install.log')
    Write-Step "Python installer exit code: $rc"
    foreach ($c in $pyCandidates) { if (Test-PythonInstall $c) { $pythonExe = $c; break } }
    if (-not $pythonExe) {
        throw "Python installer ran but stdlib is missing (Lib\encodings absent). See $logRoot\python-install.log"
    }
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH','Machine')
}
Write-Step "Using Python: $pythonExe"
# DO NOT set PYTHONHOME / PYTHONPATH here — Python derives `sys.prefix` from
# the location of `python.exe`, and explicit PYTHONPATH duplicates entries on
# `sys.path` which previously triggered `init_fs_encoding` failures.
# Clear them at BOTH process AND machine scope. A previous bad install left
# them set at machine scope which leaks into every subsequent CSE invocation
# and breaks `python.exe -m pip` with `ModuleNotFoundError: encodings`.
Remove-Item Env:\PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
[Environment]::SetEnvironmentVariable('PYTHONHOME', $null, 'Machine')
[Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'Machine')
[Environment]::SetEnvironmentVariable('PYTHONHOME', $null, 'User')
[Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'User')

# ── 3) Place ffmpeg on PATH ─────────────────────────────────────────────────
$ffmpegSrc = "$appRoot\extracted\tools\ffmpeg"
if (Test-Path $ffmpegSrc) {
    Write-Step 'Installing ffmpeg'
    if (Test-Path "$appRoot\ffmpeg") { Remove-Item "$appRoot\ffmpeg" -Recurse -Force }
    Move-Item $ffmpegSrc "$appRoot\ffmpeg" -Force
    $machinePath = [Environment]::GetEnvironmentVariable('PATH','Machine')
    if ($machinePath -notlike "*$appRoot\ffmpeg\bin*") {
        [Environment]::SetEnvironmentVariable('PATH', "$machinePath;$appRoot\ffmpeg\bin", 'Machine')
    }
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH','Machine')
} elseif (Test-Path "$appRoot\ffmpeg\bin\ffmpeg.exe") {
    Write-Step 'Reusing existing ffmpeg installation'
    $machinePath = [Environment]::GetEnvironmentVariable('PATH','Machine')
    if ($machinePath -notlike "*$appRoot\ffmpeg\bin*") {
        [Environment]::SetEnvironmentVariable('PATH', "$machinePath;$appRoot\ffmpeg\bin", 'Machine')
    }
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH','Machine')
} else {
    Write-Step '[warn] ffmpeg not present in artifact and no existing C:\lisa\ffmpeg\bin\ffmpeg.exe found.'
}

# ── 4) Place NSSM ───────────────────────────────────────────────────────────
$nssm = "$appRoot\nssm.exe"
$nssmSrc = "$appRoot\extracted\tools\nssm.exe"
if (Test-Path $nssmSrc) {
    Copy-Item $nssmSrc $nssm -Force
} elseif (Test-Path $nssm) {
    Write-Step 'Reusing existing NSSM installation'
} else {
    throw 'NSSM missing in artifact and no existing C:\lisa\nssm.exe found.'
}

# ── 4a) Native prerequisites for the Microsoft Graph calling bot media SDK ──
# These steps are MANDATORY for EchoBot.exe to start and pass media. Skipping any of
# them produces obscure runtime failures (DllNotFoundException: NativeMedia,
# MediaPerf perf-counter registration failure, TCP self-test 10060, silent
# inbound Teams audio, etc.)
#
#   (a) Visual C++ Redistributable 2015-2022 x64 - native deps of the SDK.
#   (b) Server-Media-Foundation Windows feature  - audio/video codecs.
#   (c) Windows Audio services - Teams media decode needs the audio stack.
#   (d) PowerShell 7  - the Skype Bots Media perf-counter installer
#                       (InstallMPServiceImpCounters.ps1) Imports MPServiceImp.dll
#                       which is a .NET 6 module. Windows PowerShell 5.1 (.NET
#                       Framework) cannot load .NET 6 assemblies and silently
#                       no-ops the registration => later the bot crashes with
#                       "MediaPerf is not registered: no key found at ...".
#   (e) Windows Firewall rules for 443 + 8445 inbound  - separate from NSGs.
#
# Each step is idempotent and re-running install.ps1 is safe.
function Invoke-Prereq([scriptblock]$check, [scriptblock]$install, [string]$name) {
    if (& $check) { Write-Step "[prereq] $name already present"; return }
    Write-Step "[prereq] installing $name"
    & $install
    if (-not (& $check)) { throw "prereq install failed: $name" }
}

# (a) VC++ 2015-2022 x64 redist.
Invoke-Prereq `
    -name 'VC++ 2015-2022 Redistributable (x64)' `
    -check { Test-Path 'C:\Windows\System32\msvcp140.dll' } `
    -install {
        $vcExe = Join-Path $env:TEMP 'vc_redist.x64.exe'
        $localVc = Get-ChildItem "$appRoot\extracted\tools" -Filter 'vc_redist.x64.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($localVc) {
            Copy-Item $localVc.FullName $vcExe -Force
        } else {
            Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vcExe -UseBasicParsing
        }
        Start-Process -FilePath $vcExe -ArgumentList @('/install','/quiet','/norestart') -Wait | Out-Null
    }

# (b) Server-Media-Foundation feature.
Invoke-Prereq `
    -name 'Windows Feature: Server-Media-Foundation' `
    -check { (Get-WindowsFeature -Name Server-Media-Foundation -ErrorAction SilentlyContinue).Installed } `
    -install {
        $r = Install-WindowsFeature -Name Server-Media-Foundation -ErrorAction Stop
        if ($r.RestartNeeded -eq 'Yes') { Write-Step '[warn] Server-Media-Foundation requested restart - features still active without it.' }
    }

# (c) Windows Audio services. The Graph calling media SDK can join Teams while
# still receiving silent audio if the Windows audio stack is stopped.
function Test-WindowsAudioServicesReady {
    foreach ($serviceName in @('AudioEndpointBuilder', 'audiosrv')) {
        $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
        if (-not $service) { return $false }
        if ($service.StartMode -ne 'Auto') { return $false }
        if ($service.State -ne 'Running') { return $false }
    }
    return $true
}
Invoke-Prereq `
    -name 'Windows Audio service stack' `
    -check { Test-WindowsAudioServicesReady } `
    -install {
        Set-Service -Name AudioEndpointBuilder -StartupType Automatic
        Set-Service -Name audiosrv -StartupType Automatic
        Start-Service -Name AudioEndpointBuilder
        Start-Service -Name audiosrv
    }

# (d) PowerShell 7. Required to register Microsoft Skype Bots Media perf
# counters (the registration script imports a .NET 6 module).
$pwshExe = 'C:\Program Files\PowerShell\7\pwsh.exe'
Invoke-Prereq `
    -name 'PowerShell 7' `
    -check { Test-Path $pwshExe } `
    -install {
        $msi = Join-Path $env:TEMP 'PowerShell-7-x64.msi'
        $localMsi = Get-ChildItem "$appRoot\extracted\tools" -Filter 'PowerShell-*-win-x64.msi' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($localMsi) {
            Copy-Item $localMsi.FullName $msi -Force
        } else {
            Invoke-WebRequest -Uri 'https://github.com/PowerShell/PowerShell/releases/download/v7.4.6/PowerShell-7.4.6-win-x64.msi' -OutFile $msi -UseBasicParsing
        }
        Start-Process -FilePath msiexec.exe -ArgumentList @('/i', $msi, '/quiet', '/norestart', 'ADD_PATH=1', 'REGISTER_MANIFEST=1') -Wait | Out-Null
    }

# (d) Windows Firewall (separate trust boundary from the NSG).
Invoke-Prereq `
    -name 'Firewall rule Lisa-Bot-443' `
    -check { [bool](Get-NetFirewallRule -DisplayName 'Lisa-Bot-443' -ErrorAction SilentlyContinue) } `
    -install { New-NetFirewallRule -DisplayName 'Lisa-Bot-443' -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow -Profile Any | Out-Null }
# Kestrel listens on 9441 (calling) + 9442 (signaling) behind the LB; the LB
# DNATs frontend 443 → backend 9442 and forwards probe traffic from
# 168.63.129.16 to backend 9442. Without this rule the LB probe sees the
# Windows Firewall RST and marks the backend down (rule 443 was opening
# only TCP/443 which is no longer used for ingress).
Invoke-Prereq `
    -name 'Firewall rule Lisa-Bot-Kestrel' `
    -check { [bool](Get-NetFirewallRule -DisplayName 'Lisa-Bot-Kestrel' -ErrorAction SilentlyContinue) } `
    -install { New-NetFirewallRule -DisplayName 'Lisa-Bot-Kestrel' -Direction Inbound -Protocol TCP -LocalPort 9441,9442 -Action Allow -Profile Any | Out-Null }
Invoke-Prereq `
    -name 'Firewall rule Lisa-Bot-AcmeHttp01' `
    -check { [bool](Get-NetFirewallRule -DisplayName 'Lisa-Bot-AcmeHttp01' -ErrorAction SilentlyContinue) } `
    -install {
        # Open port 80 for Let's Encrypt HTTP-01 ACME challenges. No production
        # traffic uses this port; it is only opened so that Posh-ACME's
        # WebSelfHost plugin can serve challenge tokens during cert issuance.
        New-NetFirewallRule -DisplayName 'Lisa-Bot-AcmeHttp01' -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any | Out-Null
    }
Invoke-Prereq `
    -name 'Firewall rule Lisa-Bot-Media' `
    -check { [bool](Get-NetFirewallRule -DisplayName 'Lisa-Bot-Media' -ErrorAction SilentlyContinue) } `
    -install {
        # Open the full LB media NAT pool range (8445-8544 by default).
        $endPort = $MediaNatStartPort + 99
        New-NetFirewallRule -DisplayName 'Lisa-Bot-Media' -Direction Inbound -Protocol TCP -LocalPort "$MediaNatStartPort-$endPort" -Action Allow -Profile Any | Out-Null
    }

# ── 4b) Register the Microsoft Skype Bots Media perf counters ─────────────
# MediaPlatformStartupScript.bat (shipped inside the EchoBot package) calls
# InstallMPServiceImpCounters.ps1 which Imports MPServiceImp.dll. That DLL
# requires .NET 6, so we MUST run it under pwsh 7. We invoke the .ps1 directly
# (bypassing the .bat) to avoid PATH / shell ambiguity.
$echoBotDir = "$appRoot\extracted\EchoBot"
$installCounters = Join-Path $echoBotDir 'InstallMPServiceImpCounters.ps1'
if (Test-Path $installCounters) {
    Write-Step 'Registering Skype Bots Media perf counters via pwsh 7'
    Push-Location $echoBotDir
    try {
        & $pwshExe -ExecutionPolicy Bypass -File $installCounters 2>&1 | Tee-Object -FilePath (Join-Path $logRoot 'mediaperf-install.log')
    } finally {
        Pop-Location
    }
    if (-not (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\MediaPerf\Performance')) {
        # Non-fatal: perf counter registration occasionally fails on first boot
        # (lodctr.exe race vs Windows Performance Counter service). The bot
        # itself runs fine without these counters — they only feed Azure
        # Monitor diagnostics. Throwing here would mark CSE as failed and
        # block all future runCommand operations on the instance.
        Write-Step '[warn] MediaPerf perf counters did not register (HKLM\\...\\MediaPerf\\Performance missing). Continuing - bot functions without them. See mediaperf-install.log.'
    }
} else {
    Write-Step '[warn] InstallMPServiceImpCounters.ps1 not found in EchoBot package - skipping perf counter registration'
}

# Resolve the public service FQDN before certificate selection. Priority:
#   1. $ServiceFqdn passed by CSE (LB FQDN when the LB is enabled).
#   2. The per-instance public DNS from IMDS (legacy / no-LB mode).
$resolvedFqdn = $ServiceFqdn
if ([string]::IsNullOrWhiteSpace($resolvedFqdn)) {
    try {
        $imdsHeaders = @{ Metadata = 'true' }
        $imds = Invoke-RestMethod -Method Get -Headers $imdsHeaders `
            -Uri 'http://169.254.169.254/metadata/instance?api-version=2021-12-13' -TimeoutSec 5
        $resolvedFqdn = $imds.network.interface[0].ipv4.ipAddress[0].publicIpAddress
        # If the instance has a per-instance PIP DNS label, prefer that.
        $perInstanceFqdn = $imds.compute.osProfile.computerName
        if ($perInstanceFqdn) { $resolvedFqdn = $perInstanceFqdn }
    } catch {
        Write-Step "[warn] could not resolve service FQDN from IMDS: $($_.Exception.Message)"
    }
}
if ([string]::IsNullOrWhiteSpace($resolvedFqdn)) {
    throw 'Public service FQDN is empty; refusing to select an arbitrary certificate from LocalMachine\My.'
}
Write-Step "Public service FQDN: $resolvedFqdn"

# ── 5) Resolve bot TLS cert thumbprint (synced into LM\My by AKV ext) ───────
# We pick the cert whose SAN matches the public service FQDN (or whose CN does,
# as a fallback). KV-synced certs may include older self-signed VMSS-default
# certs without a SAN — those would fail TLS validation against the Graph
# calling service and must NOT be selected.
Write-Step "Looking up TLS cert from LocalMachine\\My"
function Test-CertMatchesFqdn([System.Security.Cryptography.X509Certificates.X509Certificate2]$cert, [string]$fqdn) {
    if ([string]::IsNullOrWhiteSpace($fqdn)) { return $false }
    if ($cert.Subject -match [regex]::Escape("CN=$fqdn")) { return $true }
    foreach ($ext in $cert.Extensions) {
        if ($ext.Oid.Value -eq '2.5.29.17') {
            try {
                $san = New-Object System.Security.Cryptography.X509Certificates.X509SubjectAlternativeNameExtension($ext, $false)
                foreach ($name in $san.EnumerateDnsNames()) {
                    if ($name -eq $fqdn) { return $true }
                }
            } catch {
                # Older PowerShell: fall back to formatted text.
                if ($ext.Format($false) -match [regex]::Escape($fqdn)) { return $true }
            }
        }
    }
    return $false
}
$cert = Get-ChildItem 'Cert:\LocalMachine\My' |
    Where-Object { $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) -and (Test-CertMatchesFqdn $_ $resolvedFqdn) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1

# Fallback: if AKV VM extension didn't sync the cert (e.g. KV
# publicNetworkAccess was Disabled when the extension last ran, or no private
# endpoint), import the bot-tls-cert directly from KV via the VMSS managed
# identity. This is sustainable and survives KV access policy quirks.
if (-not $cert -and ![string]::IsNullOrWhiteSpace($resolvedFqdn)) {
    Write-Step "[warn] No cert matching '$resolvedFqdn' in LM\\My - attempting direct KV pull via MI"
    try {
        $tokResp = Invoke-RestMethod -Method Get -Headers @{ Metadata='true' } `
            -Uri 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net'
        $kvSec = Invoke-RestMethod -Method Get `
            -Uri "https://$KeyVaultName.vault.azure.net/secrets/$TlsCertSecretName?api-version=7.4" `
            -Headers @{ Authorization = "Bearer $($tokResp.access_token)" }
        $pfxBytes = [Convert]::FromBase64String($kvSec.value)
        $pfxPath = Join-Path $appRoot 'bot-tls.pfx'
        [IO.File]::WriteAllBytes($pfxPath, $pfxBytes)
        $imported = Import-PfxCertificate -FilePath $pfxPath -CertStoreLocation 'Cert:\LocalMachine\My' -Exportable
        Write-Step "Direct KV import: $($imported.Thumbprint) $($imported.Subject)"
        # Grant NETWORK SERVICE read on private key (NSSM default service account).
        try {
            $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($imported)
            $keyName = $rsa.Key.UniqueName
            $keyPath = Join-Path $env:ProgramData "Microsoft\Crypto\RSA\MachineKeys\$keyName"
            if (Test-Path $keyPath) {
                $acl = Get-Acl $keyPath
                $rule = New-Object System.Security.AccessControl.FileSystemAccessRule('NETWORK SERVICE','Read','Allow')
                $acl.AddAccessRule($rule)
                Set-Acl $keyPath $acl
            }
        } catch { Write-Step "[warn] private-key ACL grant failed: $($_.Exception.Message)" }
        # Re-select with FQDN match.
        $cert = Get-ChildItem 'Cert:\LocalMachine\My' |
            Where-Object { $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) -and (Test-CertMatchesFqdn $_ $resolvedFqdn) } |
            Sort-Object NotAfter -Descending | Select-Object -First 1
    } catch {
        Write-Step "[warn] direct KV cert pull failed: $($_.Exception.Message)"
    }
}

if (-not $cert) { throw "No usable TLS cert in LocalMachine\My matches '$resolvedFqdn' — AKV VM extension may not have synced the expected certificate yet." }
$certThumb = $cert.Thumbprint
Write-Step "TLS cert: thumbprint=$certThumb subject=$($cert.Subject)"

# ── 6) Read bot AAD secret from KV via VMSS MI ──────────────────────────────
Write-Step "Fetching bot-aad-secret from $KeyVaultName"
$kvTokenResp = Invoke-RestMethod -Method Get -Headers @{ Metadata='true' } `
    -Uri 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net'
$kvToken = $kvTokenResp.access_token
$kvHost = "$KeyVaultName.vault.azure.net"
$secretJson = Invoke-RestMethod -Method Get -Uri "https://$kvHost/secrets/bot-aad-secret?api-version=7.4" `
    -Headers @{ Authorization = "Bearer $kvToken" }
$botAadSecret = $secretJson.value
if ([string]::IsNullOrWhiteSpace($botAadSecret)) { throw 'bot-aad-secret missing or empty in KV' }

# AAD app id + tenant id can be public — also stored as KV secrets so that
# rotation/swap is centralised.
$aadAppId = (Invoke-RestMethod -Method Get -Uri "https://$kvHost/secrets/bot-aad-app-id?api-version=7.4" `
    -Headers @{ Authorization = "Bearer $kvToken" }).value
$aadTenantId = (Invoke-RestMethod -Method Get -Uri "https://$kvHost/secrets/bot-aad-tenant-id?api-version=7.4" `
    -Headers @{ Authorization = "Bearer $kvToken" }).value

# Optional: shared secret for inbound /calls/joinCall auth. Missing/empty => the
# bot skips the X-Bot-Auth check (back-compat / dev mode).
$botAuthSecret = ''
try {
    $botAuthSecret = (Invoke-RestMethod -Method Get -Uri "https://$kvHost/secrets/bot-auth-secret?api-version=7.4" `
        -Headers @{ Authorization = "Bearer $kvToken" }).value
} catch {
    Write-Step '[warn] bot-auth-secret not found in KV — /calls/joinCall will be unauthenticated.'
}

# ── 7) Set machine-wide env vars (consumed by bot + sidecar via .NET / os.environ)
# .NET config rebinds AppSettings:* from env vars using `AppSettings__Name`.
Write-Step 'Setting machine environment variables'

# Compute this instance's media public port (= MediaNatStartPort + instanceId).
# In a VMSS, IMDS exposes the resource name with the instance suffix at the end,
# e.g. computerName "<VMSS_NAME>000000" -> instanceId 0. We derive the integer
# directly from the platformFaultDomain-independent VMSS instance ID via IMDS.
$instanceId = 0
try {
    $imdsHeaders = @{ Metadata = 'true' }
    $compute = Invoke-RestMethod -Method Get -Headers $imdsHeaders `
        -Uri 'http://169.254.169.254/metadata/instance/compute?api-version=2021-12-13' -TimeoutSec 5
    if ($compute.vmScaleSetName -and $compute.name) {
        # name = e.g. "<VMSS_NAME>_0" or "<VMSS_NAME>000000"; the hex/decimal suffix is the instance id.
        if ($compute.name -match '_(\d+)$') {
            $instanceId = [int]$Matches[1]
        } elseif ($compute.name -match '([0-9a-fA-F]{6})$') {
            $instanceId = [Convert]::ToInt32($Matches[1], 16)
        }
    }
} catch {
    Write-Step "[warn] could not resolve VMSS instance id from IMDS: $($_.Exception.Message); defaulting to 0"
}
$instancePublicPort = $MediaNatStartPort + $instanceId
Write-Step "VMSS instance id: $instanceId; InstancePublicPort: $instancePublicPort"

$envMap = @{
    'AppSettings__AadAppId'                  = $aadAppId
    'AppSettings__AadAppSecret'              = $botAadSecret
    'AppSettings__AadTenantId'               = $aadTenantId
    'AppSettings__BotAuthSecret'             = $botAuthSecret
    'AppSettings__CertificateThumbprint'     = $certThumb
    'AppSettings__ServiceDnsName'            = $resolvedFqdn
    'AppSettings__ServiceCname'              = $resolvedFqdn
    'AppSettings__MediaDnsName'              = $resolvedFqdn
    'AppSettings__MediaCname'                = $resolvedFqdn
    'AppSettings__InstancePublicPort'        = "$instancePublicPort"
    'AppSettings__InstanceInternalPort'      = '8445'
    'AppSettings__BotInstanceExternalPort'   = '443'
    'AppSettings__BotInternalPort'           = '9442'
    'AppSettings__BotCallingInternalPort'    = '9441'
    'AppSettings__UseSpeechService'          = 'true'
    'AppSettings__UseAvatar'                 = 'true'
    'AppSettings__AvatarEndpoint'            = 'ws://localhost:5001'
    'AppSettings__MediaPlayoutBufferMs'      = '1000'
    'LISA_USE_PTS'                           = '1'
    'LISA_LATENCY_DIAG'                      = '0'
    'APPLICATIONINSIGHTS_CONNECTION_STRING'  = $AppInsightsConnectionString
    'AZURE_KEY_VAULT_NAME'                   = $KeyVaultName
}
foreach ($k in $envMap.Keys) {
    if ($null -ne $envMap[$k]) {
        [Environment]::SetEnvironmentVariable($k, $envMap[$k], 'Machine')
    }
}

# ── 8) Install Python sidecar deps ──────────────────────────────────────────
$sidecarDir = "$appRoot\extracted\sidecar"
if (Test-Path "$sidecarDir\requirements.txt") {
    Write-Step 'Installing sidecar Python deps'
    # Belt-and-suspenders: clear PYTHONHOME/PYTHONPATH at every scope right
    # before invoking python. The duplicated `Lib;DLLs` entries we see in the
    # `sys.path` of Python's init failure prove these env vars are leaking in
    # from somewhere (likely the CSE handler's parent process env block, which
    # captured stale Machine values BEFORE this script cleared them). Clear
    # them before invoking python so the inherited process block is clean.
    Remove-Item Env:\PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    [Environment]::SetEnvironmentVariable('PYTHONHOME', $null, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONHOME', $null, 'Machine')
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'Machine')
    [Environment]::SetEnvironmentVariable('PYTHONHOME', $null, 'User')
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'User')

    $pipLog = Join-Path $logRoot 'pip-install.log'
    Write-Step "pip log -> $pipLog"

    $requirementsPath = Join-Path $sidecarDir 'requirements.txt'
    $wheelhouseDir = Join-Path $sidecarDir 'wheelhouse'
    $offlineWheels = @()
    if (Test-Path $wheelhouseDir) {
        $offlineWheels = @(Get-ChildItem $wheelhouseDir -Filter '*.whl' -File -ErrorAction SilentlyContinue)
    }
    $allowOnlinePipFallback = [string]::Equals($env:LISA_ALLOW_ONLINE_PIP_FALLBACK, '1', [System.StringComparison]::OrdinalIgnoreCase)

    if ($offlineWheels.Count -gt 0) {
        Write-Step "Installing sidecar Python deps from local wheelhouse ($($offlineWheels.Count) wheels)"
        $pipArgs = @('-m','pip','install','--no-index','--find-links',$wheelhouseDir,'-r',$requirementsPath)
        $pipOut = "$pipLog.offline.out"
        $pipErr = "$pipLog.offline.err"
    } elseif ($allowOnlinePipFallback) {
        Write-Step '[warn] Offline wheelhouse missing; online pip fallback explicitly enabled by LISA_ALLOW_ONLINE_PIP_FALLBACK=1'
        $pipArgs = @('-m','pip','install','-r',$requirementsPath,'--default-timeout=120','--retries','5')
        $pipOut = "$pipLog.deps.out"
        $pipErr = "$pipLog.deps.err"
    } else {
        throw "Offline sidecar wheelhouse missing or empty at $wheelhouseDir. Online pip fallback is disabled by default."
    }

    $p2 = Start-Process -FilePath $pythonExe -ArgumentList $pipArgs `
        -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $pipOut -RedirectStandardError $pipErr
    if ($p2.ExitCode -ne 0) {
        Get-Content $pipErr -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        Get-Content $pipOut -ErrorAction SilentlyContinue | Select-Object -Last 30 | ForEach-Object { Write-Host $_ }
        throw "pip install failed: $($p2.ExitCode)"
    }

    # Hardening: ensure aiohttp is present even if the bundled requirements.txt
    # is stale (older builds shipped a 136-byte requirements.txt without the
    # azure-ai-voicelive[aiohttp] extra, causing the sidecar to crash with
    # "ImportError: aiohttp is required for azure-ai-voicelive").
    if ($offlineWheels.Count -gt 0) {
        $p3Args = @('-m','pip','install','--no-index','--find-links',$wheelhouseDir,'aiohttp','azure-ai-voicelive[aiohttp]')
    } elseif ($allowOnlinePipFallback) {
        $p3Args = @('-m','pip','install','aiohttp','azure-ai-voicelive[aiohttp]','--default-timeout=120','--retries','5')
    } else {
        $p3Args = @()
    }
    if ($p3Args.Count -gt 0) {
        $p3 = Start-Process -FilePath $pythonExe -ArgumentList $p3Args `
            -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput "$pipLog.aiohttp.out" -RedirectStandardError "$pipLog.aiohttp.err"
        if ($p3.ExitCode -ne 0) {
            Write-Step "[warn] aiohttp hardening install failed (exit=$($p3.ExitCode)) - sidecar may fail to start"
        }
    }
}

# ── 9) Register Windows services via NSSM ───────────────────────────────────
function Write-SidecarJobWrapper([string]$Path) {
@'
[CmdletBinding()]
param(
    [string] $PythonExe = $env:LISA_SIDECAR_PYTHON_EXE,
    [string] $ScriptPath = $env:LISA_SIDECAR_SCRIPT_PATH,
    [string] $WorkingDirectory = $env:LISA_SIDECAR_WORKING_DIRECTORY,
    [Int64] $MemoryLimitBytes = [Int64]$env:LISA_SIDECAR_MEMORY_LIMIT_BYTES
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class JobNative {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool TerminateJobObject(IntPtr hJob, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
}
"@

function Throw-LastWin32([string]$Action) {
    $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    $message = (New-Object ComponentModel.Win32Exception -ArgumentList $err).Message
    throw "$Action failed with Win32 error ${err}: $message"
}

function Get-ProcessTreeIds([int]$RootPid) {
    $all = New-Object System.Collections.Generic.List[int]
    $frontier = New-Object System.Collections.Generic.List[int]
    $all.Add($RootPid)
    $frontier.Add($RootPid)
    while ($frontier.Count -gt 0) {
        $next = New-Object System.Collections.Generic.List[int]
        foreach ($processId in $frontier) {
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $processId" -ErrorAction SilentlyContinue)
            foreach ($child in $children) {
                $childPid = [int]$child.ProcessId
                if (-not $all.Contains($childPid)) {
                    $all.Add($childPid)
                    $next.Add($childPid)
                }
            }
        }
        $frontier = $next
    }
    return @($all)
}

function Get-ProcessTreePrivateBytes([int]$RootPid) {
    $total = [Int64]0
    foreach ($processId in Get-ProcessTreeIds -RootPid $RootPid) {
        try {
            $p = Get-Process -Id $processId -ErrorAction Stop
            $total += [Int64]$p.PrivateMemorySize64
        } catch {}
    }
    return $total
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) { throw 'PythonExe is required; pass -PythonExe or set LISA_SIDECAR_PYTHON_EXE' }
if ([string]::IsNullOrWhiteSpace($ScriptPath)) { throw 'ScriptPath is required; pass -ScriptPath or set LISA_SIDECAR_SCRIPT_PATH' }
if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) { throw 'WorkingDirectory is required; pass -WorkingDirectory or set LISA_SIDECAR_WORKING_DIRECTORY' }
if (-not (Test-Path $PythonExe)) { throw "PythonExe not found: $PythonExe" }
if (-not (Test-Path $ScriptPath)) { throw "ScriptPath not found: $ScriptPath" }
if ($MemoryLimitBytes -lt 268435456) { throw "MemoryLimitBytes is too low: $MemoryLimitBytes" }
$restartThresholdBytes = [Int64]($MemoryLimitBytes * 95 / 100)

$job = [JobNative]::CreateJobObject([IntPtr]::Zero, "lisa-sidecar-$PID")
if ($job -eq [IntPtr]::Zero) { Throw-LastWin32 'CreateJobObject' }

$ptr = [IntPtr]::Zero
try {
    $limit = New-Object JobNative+JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    $limit.BasicLimitInformation.LimitFlags = 0x00000200 -bor 0x00002000
    $limit.JobMemoryLimit = [UIntPtr]::new([UInt64]$MemoryLimitBytes)
    $size = [Runtime.InteropServices.Marshal]::SizeOf($limit)
    $ptr = [Runtime.InteropServices.Marshal]::AllocHGlobal($size)
    [Runtime.InteropServices.Marshal]::StructureToPtr($limit, $ptr, $false)
    if (-not [JobNative]::SetInformationJobObject($job, 9, $ptr, [uint32]$size)) {
        Throw-LastWin32 'SetInformationJobObject'
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = '"' + $ScriptPath + '"'
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false

    $process = [System.Diagnostics.Process]::Start($psi)
    if ($null -eq $process) { throw 'Failed to start sidecar process' }
    if (-not [JobNative]::AssignProcessToJobObject($job, $process.Handle)) {
        Throw-LastWin32 'AssignProcessToJobObject'
    }

    Write-Host "[sidecar-job] started pid=$($process.Id) memoryLimitBytes=$MemoryLimitBytes restartThresholdBytes=$restartThresholdBytes"
    while (-not $process.WaitForExit(1000)) {
        $privateBytes = Get-ProcessTreePrivateBytes -RootPid $process.Id
        if ($privateBytes -ge $restartThresholdBytes) {
            Write-Warning "[sidecar-job] process tree private bytes $privateBytes reached restart threshold $restartThresholdBytes (hard limit $MemoryLimitBytes); terminating job"
            [JobNative]::TerminateJobObject($job, 137) | Out-Null
            $process.WaitForExit()
            exit 137
        }
    }

    Write-Host "[sidecar-job] exited pid=$($process.Id) exitCode=$($process.ExitCode)"
    exit $process.ExitCode
}
finally {
    if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::FreeHGlobal($ptr) }
    if ($job -ne [IntPtr]::Zero) { [JobNative]::CloseHandle($job) | Out-Null }
}
'@ | Set-Content -Path $Path -Encoding ASCII -Force
}

# NOTE: parameter must NOT be named $args — that's a PowerShell automatic
# variable inside functions and the binding silently breaks, leaving NSSM
# AppParameters empty (causes python.exe to launch as REPL, EchoBot.exe with
# no flags, etc.). Use $Arguments instead. We also call `nssm set
# AppParameters` explicitly after `nssm install` to be doubly safe.
function Register-Service([string]$name, [string]$exe, [string]$Arguments, [string]$workdir, [System.Collections.IDictionary]$env, [string]$Priority = 'NORMAL_PRIORITY_CLASS') {
    $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Step "Stopping existing service: $name"
        Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
        & $nssm remove $name confirm | Out-Null
    }
    Write-Step "Installing service: $name (exe=$exe args=$Arguments)"
    if ([string]::IsNullOrWhiteSpace($Arguments)) {
        & $nssm install $name $exe | Out-Null
    } else {
        & $nssm install $name $exe $Arguments | Out-Null
        # Explicit safety net — nssm install sometimes mis-handles quoted args.
        & $nssm set $name AppParameters $Arguments | Out-Null
    }
    & $nssm set $name AppDirectory $workdir | Out-Null
    & $nssm set $name AppStdout (Join-Path $logRoot "$name.out.log") | Out-Null
    & $nssm set $name AppStderr (Join-Path $logRoot "$name.err.log") | Out-Null
    & $nssm set $name AppRotateFiles 1 | Out-Null
    & $nssm set $name AppRotateBytes 10485760 | Out-Null
    & $nssm set $name AppPriority $Priority | Out-Null
    & $nssm set $name Start SERVICE_AUTO_START | Out-Null
    if ($env -and $env.Count -gt 0) {
        $envPairs = @($env.Keys | ForEach-Object { "$_=$($env[$_])" })
        & $nssm set $name AppEnvironmentExtra @envPairs | Out-Null
    }
    Start-Service -Name $name
}

# Bot service.
$botBin = Get-ChildItem "$appRoot\extracted\EchoBot" -Filter 'EchoBot.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($botBin) {
    Register-Service -name 'lisa-bot' -exe $botBin.FullName -Arguments '' -workdir $botBin.Directory.FullName -env @{ 'LISA_USE_PTS' = '1'; 'LISA_LATENCY_DIAG' = '0' } -Priority 'NORMAL_PRIORITY_CLASS'
} else {
    Write-Step '[warn] EchoBot.exe not found — skipping service registration'
}

# Sidecar service.
$sidecarMain = "$sidecarDir\main.py"
if (Test-Path $sidecarMain) {
    $resolvedVoiceLiveEndpoint = Normalize-VoiceLiveEndpoint $VoiceLiveEndpoint
    $missingSidecarConfig = @()
    if ([string]::IsNullOrWhiteSpace($resolvedVoiceLiveEndpoint)) { $missingSidecarConfig += 'VoiceLiveEndpoint' }
    $agentModeRequested = -not [string]::IsNullOrWhiteSpace($LisaFoundryAgentName) -or -not [string]::IsNullOrWhiteSpace($LisaFoundryProjectName) -or -not [string]::IsNullOrWhiteSpace($LisaFoundryAgentVersion)
    if ($agentModeRequested -and [string]::IsNullOrWhiteSpace($LisaFoundryAgentName)) { $missingSidecarConfig += 'LisaFoundryAgentName' }
    if ($agentModeRequested -and [string]::IsNullOrWhiteSpace($LisaFoundryProjectName)) { $missingSidecarConfig += 'LisaFoundryProjectName' }
    if ([string]::IsNullOrWhiteSpace($AzureTenantId)) { $missingSidecarConfig += 'AzureTenantId' }
    if ($missingSidecarConfig.Count -gt 0) {
        throw ('Missing sidecar Voice Live / Foundry config: ' + ($missingSidecarConfig -join ', '))
    }

    $sidecarEnv = [ordered]@{
        'AZURE_VOICELIVE_ENDPOINT'    = $resolvedVoiceLiveEndpoint
        'AZURE_VOICELIVE_API_VERSION' = $VoiceLiveApiVersion
        'AZURE_TENANT_ID'             = $AzureTenantId
        'USE_MANAGED_IDENTITY'        = '1'
        'AGENT_FOUNDRY_AGENT_NAME'     = $LisaFoundryAgentName
        'AGENT_FOUNDRY_PROJECT_NAME'   = $LisaFoundryProjectName
        'LISA_LANG'                   = $LisaLang
        'VOICELIVE_MODEL'             = 'gpt-realtime'
        'VOICELIVE_VOICE'             = $VoiceLiveVoice
        'AVATAR_CHARACTER'            = $AvatarCharacter
        'AVATAR_STYLE'                = $AvatarStyle
        'AVATAR_BACKGROUND_IMAGE_URL' = $AvatarBackgroundImageUrl
        'AVATAR_BACKGROUND_COLOR'     = $AvatarBackgroundColor
        'LISA_USE_PTS'                = '1'
        'LISA_LATENCY_DIAG'           = '0'
        'LISA_ENABLE_AVATAR_VIDEO'    = 'true'
        'LISA_FORWARD_MUXED_AUDIO'    = '1'
        'LISA_SIDECAR_JOB_MEMORY_LIMIT_BYTES' = "$sidecarJobMemoryLimitBytes"
        'LISA_TURN_DETECTION'         = 'azure_semantic_vad'
        'LISA_VAD_THRESHOLD'          = '0.35'
        'LISA_VAD_PREFIX_PADDING_MS'  = '300'
        'LISA_VAD_SPEECH_DURATION_MS' = '180'
        'LISA_VAD_SILENCE_DURATION_MS' = '700'
        'LISA_EOU_THRESHOLD_LEVEL'    = 'high'
        'LISA_EOU_TIMEOUT_MS'         = '1000'
        'VIDEO_WIDTH'                 = '640'
        'VIDEO_HEIGHT'                = '360'
        'VIDEO_BITRATE'               = '700000'
        'LISA_VIDEO_PREROLL_MAX_FRAMES' = '10'
        'LISA_MAX_FMP4_DELTA_BYTES'   = '8388608'
        'LISA_FMP4_PTS_BUFFER_MAX_BYTES' = '4194304'
        'LISA_VIDEO_DECODER_QUEUE_MAX_FRAMES' = '12'
        'LISA_VIDEO_DECODER_DRAIN_MAX_FRAMES' = '6'
        'LISA_AUDIO_DECODER_QUEUE_MAX_CHUNKS' = '200'
        'LISA_AUDIO_DECODER_DRAIN_MAX_CHUNKS' = '80'
    }
    if (-not [string]::IsNullOrWhiteSpace($LisaFoundryAgentVersion)) {
        $sidecarEnv['AGENT_FOUNDRY_AGENT_VERSION'] = $LisaFoundryAgentVersion
    }

    [Environment]::SetEnvironmentVariable('AZURE_VOICELIVE_API_KEY', $null, 'Machine')
    foreach ($k in $sidecarEnv.Keys) {
        [Environment]::SetEnvironmentVariable($k, $sidecarEnv[$k], 'Machine')
    }
    $sidecarVersionForLog = if ([string]::IsNullOrWhiteSpace($LisaFoundryAgentVersion)) { 'latest' } else { $LisaFoundryAgentVersion }
    $sidecarModeForLog = if ($agentModeRequested) { "agent=$LisaFoundryAgentName project=$LisaFoundryProjectName version=$sidecarVersionForLog" } else { 'inline-fallback' }
    $bgForLog = if ($AvatarBackgroundImageUrl) { 'image_url' } elseif ($AvatarBackgroundColor) { "color=$AvatarBackgroundColor" } else { 'default' }
    Write-Step "Sidecar Voice Live config: endpointHost=$(Get-UriHostSafe $resolvedVoiceLiveEndpoint) $sidecarModeForLog auth=managed-identity avatar=$AvatarCharacter/$AvatarStyle bg=$bgForLog avatarVideo=true forwardMuxedAudio=true usePts=true jobMemoryLimitBytes=$sidecarJobMemoryLimitBytes"
    $sidecarWrapper = Join-Path $appRoot 'sidecar-job-wrapper.ps1'
    Write-SidecarJobWrapper -Path $sidecarWrapper
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $sidecarEnv['LISA_SIDECAR_PYTHON_EXE'] = $pythonExe
    $sidecarEnv['LISA_SIDECAR_SCRIPT_PATH'] = $sidecarMain
    $sidecarEnv['LISA_SIDECAR_WORKING_DIRECTORY'] = $sidecarDir
    $sidecarEnv['LISA_SIDECAR_MEMORY_LIMIT_BYTES'] = "$sidecarJobMemoryLimitBytes"
    $sidecarArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$sidecarWrapper`""
    Register-Service -name 'lisa-sidecar' -exe $powershellExe -Arguments $sidecarArguments -workdir $sidecarDir -env $sidecarEnv -Priority 'BELOW_NORMAL_PRIORITY_CLASS'
} else {
    Write-Step '[warn] sidecar/main.py not found — skipping service registration'
}

Write-Step 'Bootstrap complete.'
Stop-Transcript
