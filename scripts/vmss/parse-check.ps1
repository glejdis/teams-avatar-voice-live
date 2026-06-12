$tokens = $null
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts/vmss/install.ps1', [ref]$tokens, [ref]$errors)
if ($errors) {
    $errors | Select-Object -First 8 | ForEach-Object {
        Write-Host ("L{0}:{1} {2}" -f $_.Extent.StartLineNumber, $_.Extent.StartColumnNumber, $_.Message)
    }
} else {
    Write-Host "parse OK"
}
