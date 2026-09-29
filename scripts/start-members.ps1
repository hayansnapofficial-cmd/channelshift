[CmdletBinding()]
param([string]$Python, [ValidateRange(1024, 65535)][int]$Port = 5189, [string]$PublicOrigin,
      [switch]$TrustProxyClientIp)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($TrustProxyClientIp -and [string]::IsNullOrWhiteSpace($PublicOrigin)) {
    throw 'TrustProxyClientIp requires PublicOrigin and a dedicated local proxy.'
}

function Assert-NoReparsePoint([string]$Path) {
    $cursor = [System.IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $entry = Get-Item -LiteralPath $cursor -Force
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Linked or reparse-point paths are not allowed.'
            }
        }
        $parent = [System.IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName
    }
}

function Assert-PrivateAcl([string]$Path, $Sid) {
    Assert-NoReparsePoint $Path
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected -or $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $Sid.Value) {
        throw 'Private mail storage ownership or ACL protection is invalid.'
    }
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -eq 0) { throw 'Private mail storage has no usable access rule.' }
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -ne $Sid.Value -or $rule.AccessControlType -ne 'Allow') {
            throw 'Private mail storage has an unexpected access rule.'
        }
    }
}

if ($env:OS -ne 'Windows_NT' -or [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'This launcher requires Windows and LOCALAPPDATA.'
}
# A PowerShell 7 parent may pass its module search path to Windows PowerShell.
# Load this shell's own security module before checking the private file ACL.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Assert-NoReparsePoint $repoRoot
if ([string]::IsNullOrWhiteSpace($Python)) { $Python = Join-Path $repoRoot '.venv\Scripts\python.exe' }
if (-not [System.IO.Path]::IsPathRooted($Python)) { throw 'Python must be an absolute executable path.' }
$Python = [System.IO.Path]::GetFullPath($Python)
Assert-NoReparsePoint $Python
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Python was not found. Create .venv or provide -Python with its absolute path.' }
$sourceRoot = Join-Path $repoRoot 'src'
Assert-NoReparsePoint $sourceRoot
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'channelshift\member_web.py') -PathType Leaf)) {
    throw 'The member server module is missing from this checkout.'
}

$localRoot = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA)
$mailRoot = [System.IO.Path]::GetFullPath((Join-Path $localRoot 'ChannelShift\mail'))
if (-not $mailRoot.StartsWith($localRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Private storage must remain inside LOCALAPPDATA.'
}
$configFile = Join-Path $mailRoot 'smtp.json'
$passwordFile = Join-Path $mailRoot 'gmail-app-password.txt'
Assert-NoReparsePoint $mailRoot
$newEnvironment = @{
    CHANNELSHIFT_SMTP_HOST = $null; CHANNELSHIFT_SMTP_PORT = $null
    CHANNELSHIFT_SMTP_FROM = $null; CHANNELSHIFT_SMTP_USER = $null
    CHANNELSHIFT_SMTP_PASSWORD_FILE = $null; CHANNELSHIFT_SMTP_MODE = $null
    PYTHONPATH = $sourceRoot
}
if (Test-Path -LiteralPath $configFile) {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    Assert-PrivateAcl $mailRoot $sid
    Assert-PrivateAcl $configFile $sid
    Assert-PrivateAcl $passwordFile $sid
    $configInfo = Get-Item -LiteralPath $configFile
    if ($configInfo.PSIsContainer -or $configInfo.Length -gt 8192) { throw 'Invalid mail configuration file.' }
    $settings = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $keys = @($settings.PSObject.Properties.Name | Sort-Object)
    $expected = @('format', 'host', 'mode', 'password_file', 'port', 'sender', 'username')
    if (($keys -join ',') -ne ($expected -join ',') -or $settings.format -ne 'channelshift.member-mail/v1' -or $settings.host -ne 'smtp.gmail.com' -or $settings.port -ne 587 -or $settings.mode -ne 'starttls') {
        throw 'Unsupported mail configuration.'
    }
    if ($settings.sender -isnot [string] -or $settings.username -isnot [string] -or $settings.sender.Length -gt 254 -or $settings.sender -notmatch '^[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$' -or $settings.username -ne $settings.sender) {
        throw 'Invalid mail sender configuration.'
    }
    if ($settings.password_file -isnot [string] -or -not [System.IO.Path]::IsPathRooted($settings.password_file) -or [System.IO.Path]::GetFullPath($settings.password_file) -ne $passwordFile) {
        throw 'Mail password must use the dedicated private file.'
    }
    $newEnvironment.CHANNELSHIFT_SMTP_HOST = $settings.host
    $newEnvironment.CHANNELSHIFT_SMTP_PORT = '587'
    $newEnvironment.CHANNELSHIFT_SMTP_FROM = $settings.sender
    $newEnvironment.CHANNELSHIFT_SMTP_USER = $settings.username
    $newEnvironment.CHANNELSHIFT_SMTP_PASSWORD_FILE = $passwordFile
    $newEnvironment.CHANNELSHIFT_SMTP_MODE = 'starttls'
    Write-Host 'Starting member mode with private SMTP settings. Email delivery has not been tested.'
} else {
    Write-Host 'Starting member mode without email configuration. Registration remains unavailable.'
}

$previousEnvironment = @{}
try {
    foreach ($name in $newEnvironment.Keys) {
        $previousEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, 'Process')
        [System.Environment]::SetEnvironmentVariable($name, $newEnvironment[$name], 'Process')
    }
    $memberArguments = @('-B', '-m', 'channelshift.member_web', '--port', [string]$Port)
    if (-not [string]::IsNullOrWhiteSpace($PublicOrigin)) {
        $memberArguments += @('--public-origin', $PublicOrigin)
    }
    if ($TrustProxyClientIp) { $memberArguments += '--trust-proxy-client-ip' }
    & $Python @memberArguments
    if ($LASTEXITCODE -ne 0) { throw 'The member server stopped with an error.' }
} finally {
    foreach ($name in $previousEnvironment.Keys) {
        [System.Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
}
