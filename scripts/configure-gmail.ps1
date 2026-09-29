[CmdletBinding()]
param([string]$Email)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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

function Set-PrivateAcl([string]$Path, [bool]$IsDirectory, $Sid) {
    if ($IsDirectory) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($Sid, 'FullControl', $inherit, 'None', 'Allow')
    } else {
        $acl = New-Object System.Security.AccessControl.FileSecurity
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($Sid, 'FullControl', 'Allow')
    }
    $acl.SetOwner($Sid)
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
    $actual = Get-Acl -LiteralPath $Path
    if (-not $actual.AreAccessRulesProtected -or $actual.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $Sid.Value) {
        throw 'Could not confirm private ownership and ACL protection.'
    }
    $rules = @($actual.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -eq 0) { throw 'Could not confirm private access rules.' }
    foreach ($access in $rules) {
        if ($access.IdentityReference.Value -ne $Sid.Value -or $access.AccessControlType -ne 'Allow') {
            throw 'Unexpected access rule on private storage.'
        }
    }
}

function Write-NewFile([string]$Path, [byte[]]$Bytes) {
    $stream = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try { $stream.Write($Bytes, 0, $Bytes.Length) } finally { $stream.Dispose() }
}

if ($env:OS -ne 'Windows_NT' -or [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'This helper requires Windows and LOCALAPPDATA.'
}
$localRoot = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA)
$mailRoot = [System.IO.Path]::GetFullPath((Join-Path $localRoot 'ChannelShift\mail'))
if (-not $mailRoot.StartsWith($localRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Private storage must remain inside LOCALAPPDATA.'
}
$passwordFile = Join-Path $mailRoot 'gmail-app-password.txt'
$configFile = Join-Path $mailRoot 'smtp.json'
Assert-NoReparsePoint $mailRoot
if ((Test-Path -LiteralPath $passwordFile) -or (Test-Path -LiteralPath $configFile)) {
    throw 'Mail configuration already exists. This helper never overwrites it.'
}
if ([string]::IsNullOrWhiteSpace($Email)) { $Email = Read-Host 'Gmail sender address' }
$Email = $Email.Trim().ToLowerInvariant()
if ($Email.Length -gt 254 -or $Email -notmatch '^[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$') {
    throw 'Enter a valid sender email address.'
}

$securePassword = Read-Host 'Gmail app password (not your Google account password)' -AsSecureString
$pointer = [IntPtr]::Zero
$passwordBytes = New-Object byte[] 16
try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $length = [Runtime.InteropServices.Marshal]::ReadInt32($pointer, -4) / 2
    $count = 0
    for ($index = 0; $index -lt $length; $index++) {
        $character = [Runtime.InteropServices.Marshal]::ReadInt16($pointer, $index * 2)
        if ($character -eq 32) { continue }
        if ($count -ge 16 -or -not (($character -ge 65 -and $character -le 90) -or ($character -ge 97 -and $character -le 122) -or ($character -ge 48 -and $character -le 57))) {
            throw 'Expected a 16-character Gmail app password; display spaces are ignored.'
        }
        $passwordBytes[$count] = [byte]$character
        $count++
    }
    if ($count -ne 16) { throw 'Expected a 16-character Gmail app password; display spaces are ignored.' }
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    [System.IO.Directory]::CreateDirectory($mailRoot) | Out-Null
    Assert-NoReparsePoint $mailRoot
    Set-PrivateAcl $mailRoot $true $sid
    Write-NewFile $passwordFile $passwordBytes
    Set-PrivateAcl $passwordFile $false $sid
    $settings = [ordered]@{
        format = 'channelshift.member-mail/v1'
        host = 'smtp.gmail.com'
        port = 587
        sender = $Email
        username = $Email
        password_file = $passwordFile
        mode = 'starttls'
    }
    $jsonBytes = [System.Text.Encoding]::UTF8.GetBytes(($settings | ConvertTo-Json))
    Write-NewFile $configFile $jsonBytes
    Set-PrivateAcl $configFile $false $sid
    Write-Host 'Private Gmail configuration saved. No email was sent. Run scripts/start-members.ps1 to start member mode.'
} finally {
    [Array]::Clear($passwordBytes, 0, $passwordBytes.Length)
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ($null -ne $securePassword) { $securePassword.Dispose() }
}
