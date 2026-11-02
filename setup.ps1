param([switch]$RegisterPi)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
& py -3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required' }
& py -3 -m venv (Join-Path $Here 'venv')
if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python venv' }
$PyExe = Join-Path $Here 'venv\Scripts\python.exe'
& $PyExe -m pip install -r (Join-Path $Here 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
$App = Join-Path $Here 'source\app.py'
& $PyExe $App catalog sync
if ($LASTEXITCODE -ne 0) { throw 'Catalog sync failed' }
Write-Host "To download ALL docsets: & `"$PyExe`" `"$App`" download --all --workers 2 --delay 2"
if ($RegisterPi) {
  if (Get-Command pi -ErrorAction SilentlyContinue) {
    & pi mcp add docsets -- $PyExe $App serve
    & pi mcp list
  } else {
    Write-Warning 'Pi CLI not found; configure your MCP host manually.'
  }
}
