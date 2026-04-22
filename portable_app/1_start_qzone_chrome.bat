@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "CHROME_EXE="

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"

if not defined CHROME_EXE (
  echo Chrome not found. Please edit this file and set the correct path.
  pause
  exit /b 1
)

start "" "%CHROME_EXE%" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%SCRIPT_DIR%.chrome-profile" ^
  https://qzone.qq.com/

echo Chrome launch command sent.
exit /b 0
