@echo off
rem R3-03B authoritative host microbenchmark driver (protocol: experiments\R3-03B-direct-cost-attribution\PROTOCOL.json).
rem Compiles patches\argine-policy-microbench.cpp + patches\argine-retention-policy.cpp with MSVC, records identities,
rem runs 3 fresh processes x 30 repetitions, then analyses. Does NOT invoke any model. Refuses an existing output dir.
setlocal EnableExtensions
set "REPO=C:\Users\alpel\Desktop\Argine4"
set "OUT=C:\Users\alpel\Desktop\Argine4-r3runs\R3-03B-microbench-run2"
set "PY=C:\Users\alpel\anaconda3\python.exe"
set "STREAM=%REPO%\experiments\R3-03B-direct-cost-attribution\routing_stream_A-4GiB.txt"
set "STREAM_SHA=0224419aa9928ef6c3bcc1698eddfffcf1ec97a88893755f030b2cd5ff5690b6"
set "EXE=%OUT%\argine-policy-microbench-r3-03b.exe"
set "VSW=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%OUT%" (echo REFUSED: output directory already exists: %OUT% & exit /b 1)
mkdir "%OUT%\raw" || exit /b 1
mkdir "%OUT%\obj" || exit /b 1
cd /D "%REPO%" || exit /b 1
for /f "usebackq delims=" %%i in (`"%VSW%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if not defined VSROOT (echo FAILED: Visual Studio C++ build tools not found via vswhere & exit /b 1)
call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" > "%OUT%\vcvars.log" 2>&1 || (echo FAILED: vcvars64 & exit /b 1)
cl > "%OUT%\cl_version.txt" 2>&1
"%PY%" "%REPO%\tools\r3_03b_check_cl_version.py" "%OUT%\cl_version.txt" || (echo FAILED: cl is not the 19.44 family used for the runtime builds - see %OUT%\cl_version.txt & exit /b 1)
set "SH="
for /f "skip=1 tokens=*" %%h in ('certutil -hashfile "%STREAM%" SHA256') do if not defined SH set "SH=%%h"
set "SH=%SH: =%"
if /I not "%SH%"=="%STREAM_SHA%" (echo FAILED: routing stream sha256 mismatch: %SH% & exit /b 1)
git -C "%REPO%" rev-parse HEAD > "%OUT%\git_head.txt"
git -C "%REPO%" status --short > "%OUT%\git_status.txt"
certutil -hashfile "%REPO%\patches\argine-policy-microbench.cpp" SHA256 > "%OUT%\source_microbench.sha256.txt"
certutil -hashfile "%REPO%\patches\argine-retention-policy.cpp" SHA256 > "%OUT%\source_policy.sha256.txt"
certutil -hashfile "%REPO%\patches\argine-retention-policy.h" SHA256 > "%OUT%\source_policy_header.sha256.txt"
certutil -hashfile "%STREAM%" SHA256 > "%OUT%\stream.sha256.txt"
echo cl /nologo /O2 /Ob2 /DNDEBUG /MT /EHsc /std:c++17 /arch:AVX2 /DNOMINMAX /W4 > "%OUT%\compiler_flags.txt"
cl /nologo /O2 /Ob2 /DNDEBUG /MT /EHsc /std:c++17 /arch:AVX2 /DNOMINMAX /W4 /I"%REPO%\patches" "%REPO%\patches\argine-policy-microbench.cpp" "%REPO%\patches\argine-retention-policy.cpp" /Fo%OUT%\obj\ /Fe:"%EXE%" > "%OUT%\compile.log" 2>&1 || (echo FAILED: compile - see %OUT%\compile.log & exit /b 1)
certutil -hashfile "%EXE%" SHA256 > "%OUT%\microbench_binary.sha256.txt"
powershell -NoProfile -Command "Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed | ConvertTo-Json" > "%OUT%\host_cpu.json" 2>&1
powercfg /getactivescheme > "%OUT%\host_power_scheme.txt" 2>&1
ver > "%OUT%\host_os.txt"
for %%N in (1 2 3) do ("%EXE%" "%STREAM%" "%OUT%\raw" %%N 30 4294967296 0.99 64 0.99 8 128 > "%OUT%\proc%%N.stdout.txt" 2> "%OUT%\proc%%N.stderr.txt" || (echo FAILED: microbenchmark process %%N - run directory is INVALID, keep it and rerun in a fresh directory & exit /b 1))
"%PY%" "%REPO%\tools\r3_03b_microbench_analyze.py" "%OUT%\raw" --expected-procs 3 --expected-reps 30 > "%OUT%\MICROBENCH_REPORT.json" || (echo FAILED: analysis & exit /b 1)
echo R3-03B MICROBENCH RUN COMPLETE: %OUT%
