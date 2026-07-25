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
    $Version = (
        py -3.11 -c "from core.version import VERSION; print(VERSION)"
    ).Trim()
    Assert-LastExitCode "读取应用版本"
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        throw "无法读取有效的 Pixkin 版本号：$Version"
    }
    py -3.11 scripts\generate_version_info.py
    Assert-LastExitCode "生成 Windows 版本资源"
    py -3.11 -m scripts.create_sample_pack
    Assert-LastExitCode "重建内置角色包"

    $PreviousQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        py -3.11 -m pytest -q `
            tests\test_build_assets.py `
            tests\test_character_package.py `
            tests\test_versioning.py
        Assert-LastExitCode "验收重建后的发布资产"
    }
    finally {
        $env:QT_QPA_PLATFORM = $PreviousQtPlatform
    }

    py -3.11 -m PyInstaller --noconfirm desktop_pet.spec
    Assert-LastExitCode "构建文件夹版"
    py -3.11 -m PyInstaller --noconfirm desktop_pet_portable.spec
    Assert-LastExitCode "构建便携版"

    $ImportPackPath = Join-Path $DistPath "Pixkin\可导入角色包"
    New-Item -ItemType Directory -Path $ImportPackPath | Out-Null
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "character-packs\yeye.zip") `
        -Destination (Join-Path $ImportPackPath "椰子.zip")

    $FolderZip = Join-Path $ReleasePath "Pixkin-$Version-win64.zip"
    Compress-Archive -Path (Join-Path $DistPath "Pixkin") -DestinationPath $FolderZip -CompressionLevel Optimal
    Copy-Item -LiteralPath (Join-Path $DistPath "Pixkin-Portable-$Version.exe") -Destination $ReleasePath
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "CHARACTER_PACKAGE_SPEC.md") -Destination $ReleasePath
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "character-packs\yeye.zip") `
        -Destination (Join-Path $ReleasePath "椰子.zip")

    $Hashes = Get-ChildItem -LiteralPath $ReleasePath -File | Get-FileHash -Algorithm SHA256
    $Hashes | ForEach-Object {
        "{0}  {1}" -f $_.Hash.ToLowerInvariant(), $_.Path.Substring($ReleasePath.Length + 1)
    } | Set-Content -LiteralPath (Join-Path $ReleasePath "SHA256SUMS.txt") -Encoding UTF8
}
finally {
    Pop-Location
}

Get-ChildItem -LiteralPath $ReleasePath | Select-Object Name, Length
