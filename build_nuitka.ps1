param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = "python"

if (-not $SkipInstall) {
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $Python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$ProjectVersion = (& $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $ProjectVersion) {
    throw "Could not read the project version from pyproject.toml."
}

$VersionParts = $ProjectVersion.Split('.')
$WindowsVersion = if ($VersionParts.Count -eq 3) { "$ProjectVersion.0" } else { $ProjectVersion }

$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

$NuitkaArgs = @(
    "-m", "nuitka",
    "--mode=onefile",
    "--enable-plugin=pyside6",
    "--windows-console-mode=attach",
    "--assume-yes-for-downloads",
    "--remove-output",
    "--output-dir=$Dist",
    "--output-filename=SynapseProfileSwitcher.exe",
    "--include-data-dir=resources=resources",
    "--include-package=synapsectrl",
    "--include-package-data=synapsectrl",
    "--include-distribution-metadata=SynapseCTRL",
    "--product-name=Synapse Profile Switcher",
    "--file-description=Automatic Razer Synapse profile switching for games",
    "--file-version=$WindowsVersion",
    "--product-version=$WindowsVersion",
    "run.py"
)

Write-Host "Building SynapseProfileSwitcher $ProjectVersion with Nuitka..."
& $Python @NuitkaArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Output = Join-Path $Dist "SynapseProfileSwitcher.exe"
if (-not (Test-Path -LiteralPath $Output -PathType Leaf)) {
    throw "Nuitka completed but $Output was not created."
}

$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Output).Hash
Write-Host ""
Write-Host "Build complete: $Output"
Write-Host "SHA256: $Hash"
