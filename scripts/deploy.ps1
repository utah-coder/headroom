<#
.SYNOPSIS
  Builds, checks and deploys Headroom in one command.

.DESCRIPTION
  Runs these steps strictly in order. Each step starts only after the previous
  one succeeded; the first failure prints the failing step's name, skips every
  later step and exits 1.

    1. Scenario builder: python -m sim.run_scenarios
       With -SkipSim: check that every scenario file exists instead, without
       touching it. The scenario list is read from SCENARIOS in
       web/src/config.ts. If that file does not exist yet, the list defaults
       to peak, sun (the two files the walking skeleton ships).
    2. Web build: npm ci, then npm run build, in web/
    3. Secret grep: exact, case-sensitive (ordinal) search for the EIA_API_KEY
       value from .env in every file under web/dist. Matching paths are
       printed; the key value never is.
    4. CDK deploy: npm ci, then
       npx cdk deploy --require-approval never --outputs-file cdk-outputs.json,
       in infra/. Afterwards the same secret grep runs over infra/cdk.out as a
       defence-in-depth check.
    5. Print SiteUrl from infra/cdk-outputs.json, then run scripts/smoke.ps1
       against it with the BucketRegionalDomainName output.

  Exit codes: 0 only when the smoke test exits 0; 1 on any failure.

  The EIA_API_KEY value is read only for the secret grep. It is held in a
  variable local to Invoke-SecretGrep, cleared after use, never printed, and
  never placed in the environment or on a command line of a child process.

.PARAMETER SkipSim
  Skip the scenario builder and deploy the existing scenario files unchanged.
#>
param([switch]$SkipSim)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

# Used only when web/src/config.ts does not exist yet (before the UI config lands).
$DefaultScenarios = @('peak', 'sun')

function Invoke-Step {
  # Runs one step and turns any error or non-zero native exit code into a
  # terminating error that names the step.
  param(
    [string]$Name,
    [scriptblock]$Body,
    [string]$WorkingDirectory
  )
  Write-Host "==> $Name"
  $pushed = $false
  $code = 0
  try {
    if ($WorkingDirectory) { Push-Location -LiteralPath $WorkingDirectory; $pushed = $true }
    $global:LASTEXITCODE = 0
    & $Body | Out-Host
    $code = $global:LASTEXITCODE
  } catch {
    throw "Step failed: $Name - $($_.Exception.Message)"
  } finally {
    if ($pushed) { Pop-Location }
  }
  if ($code -ne 0) { throw "Step failed: $Name (exit $code)" }
}

function Get-ScenarioList {
  # Returns the scenario ids from `const SCENARIOS = [...]` in web/src/config.ts,
  # or the default list when that file does not exist.
  param([string]$ConfigPath)
  if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Write-Host "    web/src/config.ts not found; using default scenario list: $($DefaultScenarios -join ', ')"
    return , $DefaultScenarios
  }
  $text = [IO.File]::ReadAllText($ConfigPath)
  $m = [regex]::Match($text, '\b(?:const|let|var)\s+SCENARIOS\b[^=\r\n]*=\s*\[([^\]]*)\]')
  if (-not $m.Success) { throw 'cannot read the SCENARIOS array from web/src/config.ts' }
  $body = $m.Groups[1].Value -replace '//[^\r\n]*', ''
  $names = @([regex]::Matches($body, '[''"`]([A-Za-z0-9_-]+)[''"`]') | ForEach-Object { $_.Groups[1].Value })
  if ($names.Count -eq 0) { throw 'the SCENARIOS array in web/src/config.ts is empty' }
  return , $names
}

function Get-EnvKey {
  # Returns the non-blank EIA_API_KEY value from a KEY=VALUE .env file, or throws.
  # The thrown message never contains the value.
  param([string]$EnvPath)
  $unavailable = 'Secret grep cannot run: EIA_API_KEY unavailable'
  if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { throw "$unavailable (.env not found)" }
  $value = $null
  foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
    $m = [regex]::Match($line, '^\s*(?:export\s+)?EIA_API_KEY\s*=(.*)$')
    if ($m.Success) { $value = $m.Groups[1].Value }   # last assignment wins
  }
  if ($null -ne $value) {
    $value = $value.Trim()
    if ($value.Length -ge 2 -and ($value[0] -eq '"' -or $value[0] -eq "'") -and $value[-1] -eq $value[0]) {
      $value = $value.Substring(1, $value.Length - 2).Trim()
    } else {
      $value = ($value -replace '\s+#.*$', '').Trim()   # unquoted inline comment
    }
  }
  if ([string]::IsNullOrWhiteSpace($value)) { throw "$unavailable (no non-empty EIA_API_KEY in .env)" }
  return $value
}

function Invoke-SecretGrep {
  # Ordinal, case-sensitive search for the key in every file under $Directory.
  # Prints matching paths only and throws if there is any match.
  param(
    [string]$Name,
    [string]$Directory,
    [string]$EnvPath
  )
  Write-Host "==> $Name"
  $key = $null
  try {
    $key = Get-EnvKey $EnvPath
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { throw "directory not found: $Directory" }
    $files = @(Get-ChildItem -LiteralPath $Directory -Recurse -File -Force)
    $hits = @(foreach ($f in $files) {
        if ([IO.File]::ReadAllText($f.FullName).Contains($key)) { $f.FullName }
      })
  } catch {
    throw "Step failed: $Name - $($_.Exception.Message)"
  } finally {
    $key = $null
  }
  if ($hits.Count -gt 0) {
    foreach ($h in $hits) { Write-Host "SECRET FOUND IN: $h" -ForegroundColor Red }
    throw "Step failed: $Name - EIA_API_KEY value found in $($hits.Count) file(s)"
  }
  Write-Host "    no match in $($files.Count) file(s)"
}

try {
  $webDir = Join-Path $root 'web'
  $infraDir = Join-Path $root 'infra'
  $envPath = Join-Path $root '.env'

  # 1. Scenario builder, or check the existing files when -SkipSim
  if (-not $SkipSim) {
    Invoke-Step 'Scenario builder' { python -m sim.run_scenarios } -WorkingDirectory $root
  } else {
    Write-Host '==> SkipSim check'
    try {
      $scenarios = Get-ScenarioList (Join-Path $webDir 'src/config.ts')
    } catch {
      throw "Step failed: SkipSim check - $($_.Exception.Message)"
    }
    $missing = @($scenarios | ForEach-Object { "web/public/scenarios/$_.json" } |
        Where-Object { -not (Test-Path -LiteralPath (Join-Path $root $_) -PathType Leaf) })
    if ($missing.Count -gt 0) {
      foreach ($f in $missing) { Write-Host "MISSING SCENARIO FILE: $f" -ForegroundColor Red }
      throw "Step failed: SkipSim check - missing $($missing -join ', ')"
    }
    Write-Host "    found $($scenarios.Count) scenario file(s): $($scenarios -join ', ')"
  }

  # 2. Web build
  Invoke-Step 'npm ci (web)' { npm ci } -WorkingDirectory $webDir
  Invoke-Step 'npm run build (web)' { npm run build } -WorkingDirectory $webDir

  # 3. Secret grep before any AWS command
  Invoke-SecretGrep -Name 'Secret grep (web/dist)' -Directory (Join-Path $webDir 'dist') -EnvPath $envPath

  # 4. CDK deploy
  Invoke-Step 'npm ci (infra)' { npm ci } -WorkingDirectory $infraDir
  $outputsPath = Join-Path $infraDir 'cdk-outputs.json'
  if (Test-Path -LiteralPath $outputsPath) { Remove-Item -LiteralPath $outputsPath }   # never read stale outputs
  Invoke-Step 'cdk deploy' { npx cdk deploy --require-approval never --outputs-file cdk-outputs.json } -WorkingDirectory $infraDir

  # Defence in depth: the key cannot reach infra/ by construction, but check the synth output anyway.
  Invoke-SecretGrep -Name 'Secret grep (infra/cdk.out)' -Directory (Join-Path $infraDir 'cdk.out') -EnvPath $envPath

  if (-not (Test-Path -LiteralPath $outputsPath -PathType Leaf)) { throw 'Step failed: cdk deploy - infra/cdk-outputs.json was not written' }
  $stackOutputs = (Get-Content -LiteralPath $outputsPath -Raw | ConvertFrom-Json).HeadroomSiteStack
  if (-not $stackOutputs -or -not $stackOutputs.SiteUrl) { throw 'Step failed: cdk deploy - SiteUrl output missing from infra/cdk-outputs.json' }
  $siteUrl = [string]$stackOutputs.SiteUrl
  $bucketDomain = [string]$stackOutputs.BucketRegionalDomainName
  Write-Host "SiteUrl: $siteUrl"

  # 5. Smoke test; its exit code decides ours
  $smoke = Join-Path $PSScriptRoot 'smoke.ps1'
  Invoke-Step 'Smoke test' { & $smoke -SiteUrl $siteUrl -BucketDomain $bucketDomain }

  Write-Host 'DEPLOY OK' -ForegroundColor Green
  exit 0
} catch {
  Write-Host "DEPLOY FAILED: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}
