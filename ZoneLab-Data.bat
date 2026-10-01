@echo off
setlocal
title Zone Lab data
rem Double-click for a menu of data steps. Advanced: ZoneLab-Data.bat pilot -Limit 50 (passes arguments to pipeline\run.ps1).
cd /d "%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"

if not "%~1"=="" (
  %PS% "pipeline\run.ps1" %*
  goto :end
)

:menu
cls
echo.
echo  Zone Lab data
echo  -------------
echo   1  One-time setup: install the replay parser (needs the .NET 10 SDK)
echo   2  Save your Osirion API key
echo   3  List recent tournaments
echo   4  Collect match IDs for one tournament window
echo   5  Download and process the collected matches
echo   6  Process my own recent replays from the Fortnite Demos folder
echo   7  Rebuild tables and reports from data already downloaded
echo   Q  Quit
echo.
set "choice="
set /p "choice=Choose a number: "
if /i "%choice%"=="1" goto :setup
if /i "%choice%"=="2" goto :key
if /i "%choice%"=="3" goto :tournaments
if /i "%choice%"=="4" goto :find
if /i "%choice%"=="5" goto :pilot
if /i "%choice%"=="6" goto :local
if /i "%choice%"=="7" goto :analyze
if /i "%choice%"=="q" goto :end
goto :menu

:setup
%PS% "pipeline\setup.ps1"
goto :done

:key
set "key="
set /p "key=Paste your Osirion API key and press Enter: "
if "%key%"=="" goto :menu
> ".env" echo OSIRION_API_KEY=%key%
echo Saved to .env (this file is never uploaded to GitHub).
goto :done

:tournaments
%PS% "pipeline\run.ps1" tournaments
goto :done

:find
set "window="
set /p "window=Event window ID (copy one from option 3): "
if "%window%"=="" goto :menu
set "players=60"
set /p "players=How many top players to pull matches from [60]: "
%PS% "pipeline\run.ps1" find -Window "%window%" -Players %players%
goto :done

:pilot
set "limit=10"
set /p "limit=How many matches to download this run [10]: "
%PS% "pipeline\run.ps1" pilot -Limit %limit%
goto :done

:local
set "count=3"
set /p "count=How many of your newest replays [3]: "
%PS% "pipeline\run.ps1" local -Count %count%
goto :done

:analyze
%PS% "pipeline\run.ps1" analyze
goto :done

:done
echo.
echo Done. If the dashboard is open, switch the data source or refresh the page to see new data.
pause
goto :menu

:end
endlocal
