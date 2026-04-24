$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$args = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onefile",
  "--windowed",
  "--collect-submodules", "mz_core",
  "--name", "mz_control_panel",
  "--distpath", $root,
  "--workpath", (Join-Path $root "build\\pyinstaller"),
  "--specpath", (Join-Path $root "build\\pyinstaller"),
  "mz_control_panel.py"
)

$driverPath = Join-Path $root "driver\\chromedriver.exe"
if (Test-Path $driverPath) {
  $args += @("--add-binary", "driver/chromedriver.exe;driver")
} else {
  Write-Host "driver/chromedriver.exe not found, build will rely on Selenium Manager at runtime."
}

& python @args
