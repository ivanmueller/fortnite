@echo off
setlocal
title Vantage data
rem Double-click for a menu of data steps. Advanced: Vantage-Data.bat pilot -Limit 50 (passes arguments to pipeline\run.ps1).
cd /d "%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"

if not "%~1"=="" (
  %PS% "pipeline\run.ps1" %*
  goto :end
)

:menu
cls
echo.
echo  Vantage data
echo  -------------
echo   1  One-time setup: install the replay parser (needs the .NET 10 SDK)
echo   2  Log in to Epic (one time; use a secondary account)
echo   3  List tournaments from the last 30 days
echo   4  Collect match IDs for one tournament window
echo   T  Weekly tier-1 collection: find, download and process this week's high-tier matches
echo   5  Download and process the collected matches (strongest lobbies first)
echo   V  Preview which matches option 5 downloads next
echo   W  Download every match from one tournament window (e.g. a LAN event)
echo   S  Survey one replay: list every kind of data it contains
echo   G  Update the parser's definitions from the newest survey (new season), then run 1
echo   6  Import replay files into your data (from a team archive, or your own matches)
echo   7  Re-process everything already downloaded (after an update)
echo   8  Download via api-fortnite.com instead (2 credits per match; use if 5 fails)
echo   9  Save an api-fortnite.com key (only needed for option 8)
echo   P  Download Epic Power Rankings (top 10,000 players, about 7 minutes)
echo   M  Download the current map's place names (for the Drop spots page)
echo   D  Store downloaded data somewhere else (e.g. another drive)
echo   K  Keep or delete raw replays after processing (saves about 95%% of disk space)
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
if /i "%choice%"=="6" goto :import
if /i "%choice%"=="7" goto :analyze
if /i "%choice%"=="8" goto :pilotapi
if /i "%choice%"=="9" goto :key
if /i "%choice%"=="l" goto :logout
if /i "%choice%"=="d" goto :datadir
if /i "%choice%"=="p" goto :pr
if /i "%choice%"=="m" goto :pois
if /i "%choice%"=="v" goto :plan
if /i "%choice%"=="w" goto :weekly
echo.
echo Tip: refresh Power Rankings first (option P) if you haven't this week; the lobby filter uses them.
set "wdays=7"
set /p "wdays=Windows that ended in the last how many days [7]: "
set "wregion="
set /p "wregion=Regions, comma-separated, e.g. NAC,EU (Enter for all): "
set "wlimit=100"
set /p "wlimit=Most matches to download this run [100]: "
set "wmin=3"
set /p "wmin=Only lobbies with at least how many PR top-1,000 players [3]: "
set "wargs=weekly -Days %wdays% -Limit %wlimit% -MinTop %wmin%"
if not "%wregion%"=="" set "wargs=%wargs% -Region %wregion%"
%PS% "pipeline\run.ps1" %wargs%
goto :done

:genexports
%PS% "pipeline\run.ps1" genexports
goto :done

:survey
set "sid="
set /p "sid=Match ID to survey (Enter for the newest download): "
if "%sid%"=="" (%PS% "pipeline\run.ps1" survey) else (%PS% "pipeline\run.ps1" survey -Path "%sid%")
goto :done

:pilotwindow
if /i "%choice%"=="s" goto :weekly
echo.
echo Tip: refresh Power Rankings first (option P) if you haven't this week; the lobby filter uses them.
set "wdays=7"
set /p "wdays=Windows that ended in the last how many days [7]: "
set "wregion="
set /p "wregion=Regions, comma-separated, e.g. NAC,EU (Enter for all): "
set "wlimit=100"
set /p "wlimit=Most matches to download this run [100]: "
set "wmin=3"
set /p "wmin=Only lobbies with at least how many PR top-1,000 players [3]: "
set "wargs=weekly -Days %wdays% -Limit %wlimit% -MinTop %wmin%"
if not "%wregion%"=="" set "wargs=%wargs% -Region %wregion%"
%PS% "pipeline\run.ps1" %wargs%
goto :done

:genexports
%PS% "pipeline\run.ps1" genexports
goto :done

:survey
if /i "%choice%"=="t" goto :weekly
if /i "%choice%"=="g" goto :genexports
if /i "%choice%"=="k" goto :keepraw
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

:import
echo.
echo Imports .replay files into your data: a team's replay archive, or your own matches.
set "ifrom="
set /p "ifrom=Folder with .replay files (Enter for the Fortnite replay folder): "
set "icount=0"
set /p "icount=How many of the newest files (Enter for all): "
if "%ifrom%"=="" (%PS% "pipeline\run.ps1" import -Count %icount%) else (%PS% "pipeline\run.ps1" import -Path "%ifrom%" -Count %icount%)
goto :done

:analyze
set "rid="
set /p "rid=Match ID to re-process (Enter for every downloaded match): "
if "%rid%"=="" (%PS% "pipeline\run.ps1" reparse) else (%PS% "pipeline\run.ps1" reparse -Path "%rid%")
goto :done

:plan
%PS% "pipeline\run.ps1" plan
goto :done

:pois
%PS% "pipeline\run.ps1" pois
goto :done

:weekly
echo.
echo Tip: refresh Power Rankings first (option P) if you haven't this week; the lobby filter uses them.
set "wdays=7"
set /p "wdays=Windows that ended in the last how many days [7]: "
set "wregion="
set /p "wregion=Regions, comma-separated, e.g. NAC,EU (Enter for all): "
set "wlimit=100"
set /p "wlimit=Most matches to download this run [100]: "
set "wmin=3"
set /p "wmin=Only lobbies with at least how many PR top-1,000 players [3]: "
set "wargs=weekly -Days %wdays% -Limit %wlimit% -MinTop %wmin%"
if not "%wregion%"=="" set "wargs=%wargs% -Region %wregion%"
%PS% "pipeline\run.ps1" %wargs%
goto :done

:genexports
%PS% "pipeline\run.ps1" genexports
goto :done

:survey
set "sid="
set /p "sid=Match ID to survey (Enter for the newest download): "
if "%sid%"=="" (%PS% "pipeline\run.ps1" survey) else (%PS% "pipeline\run.ps1" survey -Path "%sid%")
goto :done

:pilotwindow
set "window="
set /p "window=Event window ID (already collected with option 4): "
if "%window%"=="" goto :menu
%PS% "pipeline\run.ps1" pilot -Limit 1000 -Window "%window%"
goto :done

:pr
echo.
echo Reads Epic's Power Rankings leaderboard (400 pages, one per second) and rebuilds the tables.
%PS% "pipeline\run.ps1" pr
goto :done

:keepraw
echo.
echo Raw replays are only needed to re-process matches after a parser update.
echo Deleting them keeps about 3 MB per match instead of the full replay, but re-processing
echo later would need a fresh download (Epic keeps tournament replays about 30 days).
set "kr="
set /p "kr=Keep raw replays after processing? (yes/no) [yes]: "
if "%kr%"=="" set "kr=yes"
%PS% "pipeline\run.ps1" keepraw -Value %kr%
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
