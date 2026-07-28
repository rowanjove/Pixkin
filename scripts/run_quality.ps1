$ErrorActionPreference = "Stop"

function Invoke-PythonStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host "==> $Name"
    & py -3.11 @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PreviousQtPlatform = $env:QT_QPA_PLATFORM

Push-Location $ProjectRoot
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    $PythonOutput = & py -3.11 -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve the Python 3.11 executable."
    }
    $PythonExecutable = [string]($PythonOutput | Select-Object -Last 1)
    $PythonExecutable = $PythonExecutable.Trim()
    if (-not $PythonExecutable) {
        throw "Unable to resolve the Python 3.11 executable."
    }

    Invoke-PythonStep -Name "Version metadata" -Arguments @(
        "scripts\generate_version_info.py",
        "--check"
    )
    Invoke-PythonStep -Name "Compile sources" -Arguments @(
        "-m", "compileall", "-q",
        "core", "ui", "scripts", "tests", "main.py", "preview_ui.py"
    )
    Invoke-PythonStep -Name "Ruff" -Arguments @(
        "-m", "ruff", "check",
        "core", "ui", "scripts", "tests", "main.py", "preview_ui.py"
    )
    Invoke-PythonStep -Name "Pyright" -Arguments @(
        "-m", "pyright", "--pythonpath", $PythonExecutable
    )
    Invoke-PythonStep -Name "Tests and coverage" -Arguments @(
        "-m", "pytest", "-q",
        "--cov=core", "--cov=ui",
        "--cov-report=term-missing",
        "--cov-report=xml:coverage.xml",
        "--cov-fail-under=70"
    )
}
finally {
    $env:QT_QPA_PLATFORM = $PreviousQtPlatform
    Pop-Location
}
