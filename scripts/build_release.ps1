$ErrorActionPreference = "Stop"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step 失败，退出码：$LASTEXITCODE"
    }
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildPath = Join-Path $ProjectRoot "build"
$DistPath = Join-Path $ProjectRoot "dist"
$ReleasePath = Join-Path $ProjectRoot "release"

foreach ($Path in @($BuildPath, $DistPath, $ReleasePath)) {
    $FullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $FullPath.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理项目目录之外的路径：$FullPath"
    }
    if (Test-Path -LiteralPath $FullPath) {
        Remove-Item -LiteralPath $FullPath -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $ReleasePath | Out-Null

Push-Location $ProjectRoot
try {
    $VersionOutput = py -3.11 -c "from core.version import VERSION; print(VERSION)"
    Assert-LastExitCode "读取应用版本"
    $Version = [string]($VersionOutput | Select-Object -Last 1)
    $Version = $Version.Trim()
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        throw "无法读取有效的 Pixkin 版本号：$Version"
    }
    py -3.11 scripts\generate_version_info.py
    Assert-LastExitCode "生成 Windows 版本资源"
    py -3.11 -m scripts.create_sample_pack
    Assert-LastExitCode "重建内置角色包"

    powershell -NoProfile -ExecutionPolicy Bypass `
        -File scripts\run_quality.ps1
    Assert-LastExitCode "验收重建后的发布资产与质量门禁"

    py -3.11 -m PyInstaller --noconfirm desktop_pet.spec
    Assert-LastExitCode "构建文件夹版"
    py -3.11 -m PyInstaller --noconfirm desktop_pet_portable.spec
    Assert-LastExitCode "构建便携版"
    py -3.11 scripts\smoke_release.py `
        (Join-Path $DistPath "Pixkin\Pixkin.exe")
    Assert-LastExitCode "文件夹版启动退出冒烟"
    py -3.11 scripts\smoke_release.py `
        (Join-Path $DistPath "Pixkin-Portable-$Version.exe")
    Assert-LastExitCode "便携版启动退出冒烟"

    $InstallerArguments = @{}
    $TemporaryPrivateKey = $null
    try {
        if ($env:PIXKIN_UPDATE_PRIVATE_KEY_PATH) {
            $InstallerArguments.PrivateKeyPath = `
                $env:PIXKIN_UPDATE_PRIVATE_KEY_PATH
        }
        elseif ($env:PIXKIN_UPDATE_PRIVATE_KEY_B64) {
            $TemporaryPrivateKey = Join-Path `
                ([System.IO.Path]::GetTempPath()) `
                ("pixkin-update-key-" + [guid]::NewGuid().ToString("N") + ".pem")
            [System.IO.File]::WriteAllBytes(
                $TemporaryPrivateKey,
                [Convert]::FromBase64String(
                    $env:PIXKIN_UPDATE_PRIVATE_KEY_B64
                )
            )
            $InstallerArguments.PrivateKeyPath = $TemporaryPrivateKey
        }
        if ($InstallerArguments.ContainsKey("PrivateKeyPath")) {
            if (
                -not $env:PIXKIN_UPDATE_BASE_URL -or
                -not $env:PIXKIN_UPDATE_RELEASE_NOTES_URL
            ) {
                throw "更新清单签名需要 PIXKIN_UPDATE_BASE_URL 和 PIXKIN_UPDATE_RELEASE_NOTES_URL"
            }
            $InstallerArguments.BaseUrl = $env:PIXKIN_UPDATE_BASE_URL
            $InstallerArguments.ReleaseNotesUrl = `
                $env:PIXKIN_UPDATE_RELEASE_NOTES_URL
            $InstallerArguments.Channel = if (
                $env:PIXKIN_UPDATE_CHANNEL
            ) { $env:PIXKIN_UPDATE_CHANNEL } else { "stable" }
        }
        if ($env:PIXKIN_UPDATE_MANIFEST_URL) {
            $InstallerArguments.StableManifestUrl = `
                $env:PIXKIN_UPDATE_MANIFEST_URL
        }
        if ($env:PIXKIN_BETA_UPDATE_MANIFEST_URL) {
            $InstallerArguments.BetaManifestUrl = `
                $env:PIXKIN_BETA_UPDATE_MANIFEST_URL
        }
        & (Join-Path $ProjectRoot "scripts\build_installer.ps1") `
            @InstallerArguments
        Assert-LastExitCode "构建 Inno Setup 安装器"
    }
    finally {
        if (
            $TemporaryPrivateKey -and
            (Test-Path -LiteralPath $TemporaryPrivateKey)
        ) {
            Remove-Item -LiteralPath $TemporaryPrivateKey -Force
        }
    }

    $ImportPackPath = Join-Path $DistPath "Pixkin\可导入角色包"
    New-Item -ItemType Directory -Path $ImportPackPath | Out-Null
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "character-packs\yeye.zip") `
        -Destination (Join-Path $ImportPackPath "椰子.zip")

    $FolderZip = Join-Path $ReleasePath "Pixkin-$Version-win64.zip"
    Compress-Archive -Path (Join-Path $DistPath "Pixkin") -DestinationPath $FolderZip -CompressionLevel Optimal
    Copy-Item -LiteralPath (Join-Path $DistPath "Pixkin-Portable-$Version.exe") -Destination $ReleasePath
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "CHARACTER_PACKAGE_SPEC.md") -Destination $ReleasePath
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "character-packs\yeye.zip") `
        -Destination (Join-Path $ReleasePath "yeye.zip")
    foreach ($Name in @("shanshan.zip", "linlin.zip", "pip.zip")) {
        Copy-Item -LiteralPath `
            (Join-Path $ProjectRoot "character-packs\$Name") `
            -Destination (Join-Path $ReleasePath $Name)
    }
    py -3.11 -m pip_audit -r requirements-lock.txt `
        --format cyclonedx-json `
        --output (Join-Path $ReleasePath "SBOM.cdx.json")
    Assert-LastExitCode "生成依赖审计与 SBOM"

    $Hashes = Get-ChildItem -LiteralPath $ReleasePath -File | Get-FileHash -Algorithm SHA256
    $Hashes | ForEach-Object {
        "{0}  {1}" -f $_.Hash.ToLowerInvariant(), $_.Path.Substring($ReleasePath.Length + 1)
    } | Set-Content -LiteralPath (Join-Path $ReleasePath "SHA256SUMS.txt") -Encoding UTF8
}
finally {
    Pop-Location
}

Get-ChildItem -LiteralPath $ReleasePath | Select-Object Name, Length
