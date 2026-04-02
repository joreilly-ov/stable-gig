param(
    [switch]$SkipHtml,
    [switch]$SkipJunit
)

$ErrorActionPreference = "Stop"

function Invoke-CheckedCommand {
    param([scriptblock]$Command, [string]$ErrorMessage)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$outputDir = Join-Path $repoRoot "docs/generated"

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

Push-Location $backendDir
try {
    Write-Host "Generating OpenAPI schema from FastAPI app..."
    Invoke-CheckedCommand { python (Join-Path $repoRoot "scripts/generate_openapi.py") } "OpenAPI generation failed"

    Write-Host "Collecting pytest inventory..."
    $inventoryOutput = Invoke-CheckedCommand { python -m pytest --collect-only -q } "Pytest inventory collection failed"
    $inventoryLines = $inventoryOutput | Where-Object { $_ -match '^tests/' }
    $inventoryLines | Out-File -FilePath (Join-Path $outputDir "test-inventory.txt") -Encoding utf8

    if (-not $SkipJunit) {
        Write-Host "Running pytest to produce JUnit XML evidence..."
        Invoke-CheckedCommand { python -m pytest --junitxml (Join-Path $outputDir "tests-junit.xml") } "Pytest JUnit generation failed" | Out-Null
    }
}
finally {
    Pop-Location
}

Write-Host "Generating API feature matrix from routes and tests..."
Invoke-CheckedCommand { python (Join-Path $repoRoot "scripts/generate_feature_matrix.py") } "Feature matrix generation failed"

if (-not $SkipHtml) {
    if (Get-Command npx -ErrorAction SilentlyContinue) {
        Write-Host "Generating human-readable API docs (ReDoc)..."
        Invoke-CheckedCommand { npx --yes @redocly/cli build-docs (Join-Path $outputDir "openapi.json") -o (Join-Path $outputDir "api.html") } "ReDoc HTML generation failed"
    }
    else {
        Write-Warning "npx is not installed; skipping docs/generated/api.html generation."
    }
}

Write-Host "Documentation generation complete. Outputs are in docs/generated/."
