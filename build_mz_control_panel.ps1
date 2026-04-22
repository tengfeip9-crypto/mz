$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --collect-submodules mz_core `
  --name mz_control_panel `
  --distpath $root `
  --workpath (Join-Path $root "build\\pyinstaller") `
  --specpath (Join-Path $root "build\\pyinstaller") `
  --add-binary "driver/chromedriver.exe;driver" `
  mz_control_panel.py
