param([string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\ChannelShift Independent'))
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$releaseVersion = & python -B (Join-Path $PSScriptRoot 'build-release.py') --version
if ($LASTEXITCODE -ne 0 -or $releaseVersion -notmatch '^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?$') { throw 'Package version could not be verified.' }
$wheelPath = Join-Path $repository ("release\channelshift-{0}-py3-none-any.whl" -f $releaseVersion)
if (!(Test-Path -LiteralPath $wheelPath -PathType Leaf)) { throw 'Build the release first or install the published wheel manually.' }
$destination = [IO.Path]::GetFullPath($InstallDirectory)
$environmentPath = Join-Path $destination 'venv'
if (Test-Path -LiteralPath $environmentPath) { throw 'An environment already exists. It was not replaced. Keep it or select another installation directory.' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
& python -m venv $environmentPath
if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
$installedPython = Join-Path $environmentPath 'Scripts\python.exe'
& $installedPython -m pip install --index-url https://pypi.org/simple --disable-pip-version-check $wheelPath
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. No shortcut was created.' }
& $installedPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcutFolders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($shortcutFolder in $shortcutFolders) {
    $shortcutPath = Join-Path $shortcutFolder '채널쉬프트 독립판.lnk'
    if (Test-Path -LiteralPath $shortcutPath) { throw 'An independent studio shortcut already exists. It was not overwritten.' }
    $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $environmentPath 'Scripts\pythonw.exe'
    $shortcut.Arguments = '-m channelshift.launcher'
    $shortcut.WorkingDirectory = $destination
    $shortcut.Description = 'ChannelShift independent database starter studio'
    $shortcut.Save()
}
@{
    version = $releaseVersion
    package_sha256 = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    installed_utc = (Get-Date).ToUniversalTime().ToString('o')
    studio_url = 'http://127.0.0.1:5187/'
    mcp_command = (Join-Path $environmentPath 'Scripts\channelshift-mcp.exe')
    data_directory = '~/.channelshift (or CHANNELSHIFT_HOME)'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'installation.json') -Encoding utf8
Write-Output 'Installed ChannelShift Independent. Existing applications and schema data were preserved.'
Write-Output ('MCP command: ' + (Join-Path $environmentPath 'Scripts\channelshift-mcp.exe'))
