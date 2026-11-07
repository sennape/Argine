@echo off
REM Avvia Argine senza installarlo: argine.bat <comando> [opzioni]
setlocal
cd /d "%~dp0"
python -m argine %*
set ARGINE_EXITCODE=%ERRORLEVEL%
REM `endlocal` can itself reset ERRORLEVEL, and without an explicit
REM `exit /b`, a .bat file's own exit status (as seen by a caller
REM chaining with `&&`) can end up reflecting the last internal command
REM (endlocal) rather than the python process -- silently turning a
REM non-zero research2 result (e.g. CAPTURE_FAILED) into an apparent
REM success and letting a chained `&& next-command` run anyway. Capture
REM the real exit code into a variable before `endlocal`, then exit with
REM it explicitly, in one statement so the captured value survives.
endlocal & exit /b %ARGINE_EXITCODE%
