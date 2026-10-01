@echo off
setlocal
title Vantage
rem Double-click to start Vantage and open the dashboard in Chrome.
rem Close this window (or press Ctrl+C) to stop it.
cd /d "%~dp0"

where node >nul 2>nul || (
  echo Node.js is not installed. Install the LTS version from https://nodejs.org and try again.
  pause
  exit /b 1
)

rem Already running? Just open the dashboard instead of starting a second copy.
netstat -ano | findstr /r /c:":5173 .*LISTENING" >nul && (
  echo Vantage is already running. Opening the dashboard...
  call :open
  exit /b 0
)

rem First run on this machine: install everything.
set NEEDS_SETUP=
if not exist "node_modules\" set NEEDS_SETUP=1
if not exist "web\node_modules\" set NEEDS_SETUP=1
if not exist ".venv\" set NEEDS_SETUP=1
if defined NEEDS_SETUP (
  echo First run: installing dependencies. This takes a few minutes...
  call npm run setup || goto :failed
)

rem Open Chrome as soon as the dashboard answers (falls back to the default browser).
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest 'http://localhost:5173' -UseBasicParsing -TimeoutSec 1 | Out-Null; break}catch{Start-Sleep -Milliseconds 500}}; try{Start-Process chrome 'http://localhost:5173' -ErrorAction Stop}catch{Start-Process 'http://localhost:5173'}"

rem Stop Vite opening a second tab in the default browser.
set BROWSER=none
echo Starting Vantage at http://localhost:5173
echo Close this window or press Ctrl+C to stop it.
echo.
call npm run dev
exit /b %errorlevel%

:open
powershell -NoProfile -Command "try{Start-Process chrome 'http://localhost:5173' -ErrorAction Stop}catch{Start-Process 'http://localhost:5173'}"
exit /b 0

:failed
echo.
echo Something went wrong. Scroll up for the error message, or run "npm run setup" in this folder.
pause
exit /b 1
