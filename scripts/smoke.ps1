<#
.SYNOPSIS
  Smoke-tests the deployed Headroom site.

.DESCRIPTION
  Runs three checks and always runs all of them:
    1. GET <SiteUrl>/                      -> 200
    2. GET <SiteUrl>/scenarios/peak.json   -> 200 and the full body parses as JSON
    3. GET https://<BucketDomain>/index.html (direct S3, unauthenticated) -> 403

  Prints "name | status | PASS/FAIL" per check. A request with no response
  within 15 seconds is recorded as TIMEOUT; a connection failure as NETWORK_ERROR.

  Exit codes: 0 = all checks passed, 1 = one or more checks failed,
              2 = SiteUrl missing or invalid (no requests sent).

  Uses System.Net.Http.HttpClient so status handling and the timeout behave the
  same on Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER SiteUrl
  The CloudFront site URL, e.g. https://d111111abcdef8.cloudfront.net

.PARAMETER BucketDomain
  The site bucket's regional domain name. If omitted, it is read from the
  HeadroomSiteStack output BucketRegionalDomainName via the AWS CLI.
#>
param(
  [string]$SiteUrl,
  [string]$BucketDomain
)

# No Mandatory parameter: a missing SiteUrl must fail fast without prompting (Req 3.6).
if ([string]::IsNullOrWhiteSpace($SiteUrl)) {
  Write-Host 'ERROR: SiteUrl is missing. Usage: smoke.ps1 -SiteUrl <https://...> [-BucketDomain <bucket.s3.region.amazonaws.com>]'
  exit 2
}

$SiteUrl = $SiteUrl.Trim().TrimEnd('/')
$siteUri = $null
if (-not [Uri]::TryCreate($SiteUrl, [UriKind]::Absolute, [ref]$siteUri) -or
    ($siteUri.Scheme -ne 'http' -and $siteUri.Scheme -ne 'https')) {
  Write-Host "ERROR: SiteUrl is not a valid absolute http(s) URL: $SiteUrl"
  exit 2
}

Add-Type -AssemblyName System.Net.Http

# Windows PowerShell 5.1 (.NET Framework) may not offer TLS 1.2 by default; CloudFront and S3 require it.
if ($PSVersionTable.PSEdition -ne 'Core') {
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
}

function Get-BucketDomainFromStack {
  # Returns the BucketRegionalDomainName output, or $null if it cannot be read.
  try {
    $raw = & aws cloudformation describe-stacks --stack-name HeadroomSiteStack --output json 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
    $stack = ($raw -join "`n" | ConvertFrom-Json).Stacks | Select-Object -First 1
    $output = $stack.Outputs | Where-Object { $_.OutputKey -eq 'BucketRegionalDomainName' } | Select-Object -First 1
    if ($output -and $output.OutputValue) { return [string]$output.OutputValue }
  } catch { }
  return $null
}

function Get-FailureStatus([System.Exception]$err) {
  # Walk the exception chain: HttpClient timeouts surface as TaskCanceledException
  # (with an inner TimeoutException on .NET 5+); everything else is a network error.
  for ($e = $err; $null -ne $e; $e = $e.InnerException) {
    if ($e -is [System.OperationCanceledException] -or $e -is [System.TimeoutException]) { return 'TIMEOUT' }
  }
  return 'NETWORK_ERROR'
}

function Invoke-Check {
  param(
    [string]$Name,
    [string]$Url,
    [System.Net.Http.HttpClient]$Client,
    [scriptblock]$Evaluate   # param($statusCode, $body) -> $true if the check passes
  )
  $response = $null
  try {
    $response = $Client.GetAsync($Url).GetAwaiter().GetResult()
    $code = [int]$response.StatusCode
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $pass = [bool](& $Evaluate $code $body)
    return [pscustomobject]@{ Name = $Name; Status = [string]$code; Pass = $pass }
  } catch {
    $inner = $_.Exception; while ($inner.InnerException) { $inner = $inner.InnerException }
    return [pscustomobject]@{ Name = $Name; Status = (Get-FailureStatus $_.Exception); Pass = $false; Detail = $inner.Message }
  } finally {
    if ($response) { $response.Dispose() }
  }
}

if ([string]::IsNullOrWhiteSpace($BucketDomain)) {
  $BucketDomain = Get-BucketDomainFromStack
}

$handler = New-Object System.Net.Http.HttpClientHandler
$http = New-Object System.Net.Http.HttpClient($handler)
$http.Timeout = [TimeSpan]::FromSeconds(15)

$results = @()
try {
  # 1. Site root
  $results += Invoke-Check -Name 'GET /' -Url "$SiteUrl/" -Client $http -Evaluate {
    param($code, $body) $code -eq 200
  }

  # 2. Scenario JSON: status 200 alone is not enough, because the 403 -> /index.html
  #    error response would return the HTML page with 200 for a missing file (Req 3.2).
  $results += Invoke-Check -Name 'GET /scenarios/peak.json' -Url "$SiteUrl/scenarios/peak.json" -Client $http -Evaluate {
    param($code, $body)
    if ($code -ne 200 -or [string]::IsNullOrWhiteSpace($body)) { return $false }
    try { $null = $body | ConvertFrom-Json -ErrorAction Stop; return $true } catch { return $false }
  }

  # 3. Direct S3 object URL must be denied (Req 1.13)
  if ([string]::IsNullOrWhiteSpace($BucketDomain)) {
    $results += [pscustomobject]@{ Name = 'GET direct S3 index.html'; Status = 'NO_BUCKET_DOMAIN'; Pass = $false }
  } else {
    $bucketHost = $BucketDomain.Trim() -replace '^https?://', '' -replace '/+$', ''
    $results += Invoke-Check -Name 'GET direct S3 index.html' -Url "https://$bucketHost/index.html" -Client $http -Evaluate {
      param($code, $body) $code -eq 403
    }
  }
} finally {
  $http.Dispose()
}

foreach ($r in $results) {
  $verdict = if ($r.Pass) { 'PASS' } else { 'FAIL' }
  Write-Host ('{0} | {1} | {2}' -f $r.Name, $r.Status, $verdict)
}

$failed = @($results | Where-Object { -not $_.Pass })
$passedCount = $results.Count - $failed.Count
if ($failed.Count -gt 0) {
  Write-Host ("SMOKE FAILED: {0} of {1} checks passed" -f $passedCount, $results.Count)
  foreach ($f in $failed) {
    $line = "  FAILED: {0} (status {1})" -f $f.Name, $f.Status
    if ($f.PSObject.Properties['Detail'] -and $f.Detail) { $line += " - $($f.Detail)" }
    Write-Host $line
  }
  exit 1
}

Write-Host '3 of 3 checks passed'
exit 0
