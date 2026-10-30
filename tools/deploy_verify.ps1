$ErrorActionPreference = 'Stop'
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Project 'ai-calling\.deploy-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Deploy venv is missing' }
if (-not $env:AI_CALLING_PUBLIC_HOST) { throw 'AI_CALLING_PUBLIC_HOST is missing' }
if ($env:AI_CALLING_PUBLIC_HOST -match 'lhr\.life$') { throw 'Ephemeral localhost.run host is not deployment-ready' }
& $Python -m unittest discover -s (Join-Path $Project 'ai-calling\tests') -v
if ($LASTEXITCODE) { throw 'Project tests failed' }
& $Python (Join-Path $Project 'ai-calling\server\provider_preflight.py')
if ($LASTEXITCODE) { throw 'Provider preflight failed' }
$health = Invoke-RestMethod 'http://127.0.0.1:8001/healthz' -TimeoutSec 5
$ready = Invoke-RestMethod 'http://127.0.0.1:8001/readyz' -TimeoutSec 5
if ($health.status -ne 'ok' -or -not $ready.ready) { throw 'Service is not ready' }
'DEPLOYMENT_VERIFY_PASS (no dial performed)'
