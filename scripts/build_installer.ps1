param(
    [string]$PrivateKeyPath = "",
    [ValidateSet("stable", "beta")]
    [string]$Channel = "stable",
    [string]$BaseUrl = "",
    [string]$ReleaseNotesUrl = "",
    [string]$StableManifestUrl = "",
    [string]$BetaManifestUrl = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Version = (
    py -3.11 -c "from core.version import VERSION; print(VERSION)"
).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "无法读取有效的 Pixkin 版本号"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "dist\Pixkin\Pixkin.exe"))) {
    throw "请先构建 dist\Pixkin\Pixkin.exe"
}
$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($null -eq $Iscc) {
    $Candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $IsccPath = $Candidates |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
        Select-Object -First 1
    if (-not $IsccPath) {
        throw "未找到 Inno Setup 6（ISCC.exe）"
    }
} else {
    $IsccPath = $Iscc.Source
}

$env:PIXKIN_PROJECT_ROOT = $ProjectRoot
$env:PIXKIN_INSTALLER_VERSION = $Version
$InstallModePath = Join-Path $ProjectRoot "build\installer\install-mode.json"
New-Item -ItemType Directory -Path (Split-Path $InstallModePath) -Force |
    Out-Null
@{
    mode = "installed"
    app_id = "Pixkin.Desktop"
    update_manifest_urls = @{
        stable = $StableManifestUrl
        beta = $BetaManifestUrl
    }
} | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath $InstallModePath -Encoding UTF8
$env:PIXKIN_INSTALL_MODE_SOURCE = $InstallModePath
try {
    & $IsccPath (Join-Path $ProjectRoot "installer\Pixkin.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup 构建失败，退出码：$LASTEXITCODE"
    }
} finally {
    Remove-Item Env:\PIXKIN_PROJECT_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:\PIXKIN_INSTALLER_VERSION -ErrorAction SilentlyContinue
    Remove-Item Env:\PIXKIN_INSTALL_MODE_SOURCE -ErrorAction SilentlyContinue
}

$Installer = Join-Path $ProjectRoot "release\Pixkin-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
    throw "安装器未生成：$Installer"
}
if ($PrivateKeyPath) {
    if (-not $BaseUrl -or -not $ReleaseNotesUrl) {
        throw "签名更新清单时必须提供 BaseUrl 和 ReleaseNotesUrl"
    }
    py -3.11 (Join-Path $ProjectRoot "scripts\sign_update_manifest.py") `
        --private-key $PrivateKeyPath `
        --public-key (Join-Path $ProjectRoot "assets\update-public-key.pem") `
        --installer $Installer `
        --output (Join-Path $ProjectRoot "release\update-$Channel.json") `
        --channel $Channel `
        --base-url $BaseUrl `
        --release-notes-url $ReleaseNotesUrl
    if ($LASTEXITCODE -ne 0) {
        throw "更新清单签名失败，退出码：$LASTEXITCODE"
    }
}

Get-Item -LiteralPath $Installer | Select-Object Name, Length
