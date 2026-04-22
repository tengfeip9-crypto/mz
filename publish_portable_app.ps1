$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$portableDir = Join-Path $root "portable_app"
$sourceExe = Join-Path $root "mz_control_panel.exe"
$targetExe = Join-Path $portableDir "MZ_Control_Panel.exe"

if (-not (Test-Path $sourceExe)) {
  throw "Cannot find source exe: $sourceExe"
}

New-Item -ItemType Directory -Force -Path $portableDir | Out-Null
Copy-Item -LiteralPath $sourceExe -Destination $targetExe -Force

Write-Host "Portable app updated:" $targetExe
