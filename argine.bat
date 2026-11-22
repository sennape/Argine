@echo off
REM Avvia Argine senza installarlo: argine.bat <comando> [opzioni]
setlocal
cd /d "%~dp0"
python -m argine %*
REM `exit /b N`, unlike a bare `exit`, ends only THIS batch script's own
REM execution (returning control to an interactive session or to a
REM caller's `&&`/`||` chain) and implicitly performs ENDLOCAL as part
REM of ending the script -- there is no need for, and no race against,
REM a separate explicit `endlocal` here. An earlier version chained
REM `endlocal & exit /b %VAR%` on one line specifically to work around
REM `endlocal` resetting ERRORLEVEL before it could be read; that relied
REM on `&` sequencing and parse-time `%` expansion happening in exactly
REM the right order. Dropping the explicit `endlocal` removes that
REM dependency entirely: nothing runs between the python call and this
REM `exit /b`, so `%ERRORLEVEL%` here is still exactly python's own exit
REM code, and ENDLOCAL happens automatically afterward.
exit /b %ERRORLEVEL%
