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
echo   2  Log in to Epic (one time; use a secondary account)
echo   3  List tournaments from the last 30 days
echo   4  Collect match IDs for one tournament window
echo   5  Download and process the collected matches (free, from Epic)
echo   6  Process my own recent replays from the Fortnite Demos folder
echo   7  Re-process everything already downloaded (after an update)
echo   8  Download via api-fortnite.com instead (2 credits per match; use if 5 fails)
echo   9  Save an api-fortnite.com key (only needed for option 8)
echo   D  Store downloaded data somewhere else (e.g. another drive)
echo   L  Log out of Epic (revokes the saved login)
echo   Q  Quit
echo.
set "choice="
set /p "choice=Choose a number: "
if /i "%choice%"=="1" goto :setup
if /i "%choice%"=="2" goto :login
if /i "%choice%"=="3" goto :tournaments
if /i "%choice%"=="4" goto :find
if /i "%choice%"=="5" goto :pilot
if /i "%choice%"=="6" goto :local
if /i "%choice%"=="7" goto :analyze
if /i "%choice%"=="8" goto :pilotapi
if /i "%choice%"=="9" goto :key
if /i "%choice%"=="l" goto :logout
if /i "%choice%"=="d" goto :datadir
if /i "%choice%"=="q" goto :end
goto :menu

:setup
%PS% "pipeline\setup.ps1"
goto :done

:login
echo.
echo Use a SECONDARY Epic account, not your main one.
%PS% "pipeline\run.ps1" login
goto :done

:logout
%PS% "pipeline\run.ps1" logout
goto :done

:key
set "key="
set /p "key=Paste your api-fortnite.com API key and press Enter: "
if "%key%"=="" goto :menu
> ".env" echo FORTNITE_API_KEY=%key%
echo Saved to .env (this file is never uploaded to GitHub). Checking it works...
%PS% "pipeline\run.ps1" test -Source api-fortnite
goto :done

:tournaments
set "region="
set /p "region=Region filter, e.g. EU or NAC (Enter for all): "
set "search="
set /p "search=Name filter, e.g. cash or fncs (Enter for all): "
set "targs=tournaments"
if not "%region%"=="" set "targs=%targs% -Region %region%"
if not "%search%"=="" set "targs=%targs% -Search %search%"
%PS% "pipeline\run.ps1" %targs%
goto :done

:find
set "window="
set /p "window=Event window ID (copy one from option 3): "
if "%window%"=="" goto :menu
set "pages=10"
set /p "pages=Leaderboard pages to read: a number, or all [10]: "
%PS% "pipeline\run.ps1" find -Window "%window%" -Pages %pages%
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
%PS% "pipeline\run.ps1" reparse
goto :done

:datadir
echo.
echo Pick a NEW or EMPTY folder, for example D:\ZoneLabData (not a folder that already holds other files).
echo Everything downloaded so far moves there, and future downloads go there too.
set "dpath="
set /p "dpath=Folder: "
if "%dpath%"=="" goto :menu
%PS% "pipeline\run.ps1" datadir -Path "%dpath%"
goto :done

:pilotapi
set "limit=5"
set /p "limit=How many matches to download via api-fortnite.com [5] (free tier: 15 credits/day = 7 matches): "
%PS% "pipeline\run.ps1" pilot -Limit %limit% -Via api-fortnite
goto :done

:done
echo.
echo Done. If the dashboard is open, switch the data source or refresh the page to see new data.
pause
goto :menu

:end
endlocal
