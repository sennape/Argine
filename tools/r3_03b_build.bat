@echo off
rem R3-03B instrumented runtime build (NEW identity). Prepares C:\Users\alpel\Desktop\Argine4-r3build-r3-03b\llama.cpp from the
rem frozen tree, overlays the committed R3-03B sources, configures with the frozen R3 configuration, builds llama-server,
rem and proves the R3-03A and frozen runtimes are untouched. Refuses to run if the new tree already exists.
rem Does NOT run any model. Not run by the agent.
setlocal EnableExtensions
set "REPO=C:\Users\alpel\Desktop\Argine4"
set "PY=C:\Users\alpel\anaconda3\python.exe"
set "FROZEN=C:\Users\alpel\Desktop\Argine4-r3build\llama.cpp"
set "FROZEN_EXE=C:\Users\alpel\Desktop\Argine4-r3build\llama.cpp\build\bin\llama-server.exe"
set "FROZEN_SHA=3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644"
set "R3A_EXE=C:\Users\alpel\Desktop\Argine4-r3build-r3-03a\llama.cpp\build-r3-03a\bin\llama-server.exe"
set "R3A_SHA=e935005e5131b29d12b113b000fa52e798b4b4419a23724ec70af3f6875fcd76"
set "NEWROOT=C:\Users\alpel\Desktop\Argine4-r3build-r3-03b"
set "NEW=%NEWROOT%\llama.cpp"
set "BLD=%NEW%\build-r3-03b"
set "REC=%NEWROOT%\BUILD_RECORD"
set "VSW=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%NEWROOT%" (echo REFUSED: %NEWROOT% already exists & exit /b 1)
mkdir "%REC%" || exit /b 1
cd /D "%REPO%" || exit /b 1
git -C "%REPO%" rev-parse HEAD > "%REC%\repo_head.txt"
git -C "%REPO%" status --short > "%REC%\repo_status.txt"
for /f "usebackq delims=" %%i in (`"%VSW%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if not defined VSROOT (echo FAILED: Visual Studio C++ build tools not found via vswhere & exit /b 1)
call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" > "%REC%\vcvars.log" 2>&1 || (echo FAILED: vcvars64 & exit /b 1)
cl > "%REC%\cl_version.txt" 2>&1
"%PY%" "%REPO%\tools\r3_03b_check_cl_version.py" "%REC%\cl_version.txt" || (echo FAILED: cl is not the 19.44 family & exit /b 1)
cmake --version > "%REC%\cmake_version.txt" 2>&1 || (echo FAILED: cmake not found after vcvars64 & exit /b 1)
ninja --version > "%REC%\ninja_version.txt" 2>&1 || (echo FAILED: ninja not found after vcvars64 & exit /b 1)
robocopy "%FROZEN%" "%NEW%" /E /XD "%FROZEN%\build" /NFL /NDL /NP /NJH > "%REC%\robocopy_tree.log" 2>&1
if errorlevel 8 (echo FAILED: robocopy of the frozen tree - see %REC%\robocopy_tree.log & exit /b 1)
for %%F in (argine-experts.cpp argine-store.cpp argine-store.h argine-retention-policy.cpp argine-retention-policy.h) do copy /Y "%REPO%\patches\%%F" "%NEW%\ggml\src\ggml-cpu\%%F" > nul || (echo FAILED: overlay %%F & exit /b 1)
"%PY%" "%REPO%\tools\r3_03b_build_verify.py" --stage pre --repo "%REPO%" --tree "%NEW%" > "%REC%\overlay_verify.json" || (echo FAILED: overlay verification - see %REC%\overlay_verify.json & exit /b 1)
cd /D "%NEW%" || exit /b 1
cmake -S . -B build-r3-03b -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_BMI2=ON -DGGML_OPENMP=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DCMAKE_CXX_FLAGS=/DNOMINMAX -DCMAKE_C_FLAGS=/DNOMINMAX > "%REC%\configure.log" 2>&1 || (echo FAILED: configure - see %REC%\configure.log & exit /b 1)
cmake --build build-r3-03b --target llama-server -j 8 > "%REC%\build_attempt1.log" 2>&1
if not errorlevel 1 goto built
echo Attempt 1 failed (expected: UI asset provisioning). Applying the build-only UI asset workaround, then retrying.
if not exist "%BLD%\tools\ui" mkdir "%BLD%\tools\ui"
robocopy "%FROZEN%\build\tools\ui\dist" "%BLD%\tools\ui\dist" /E /NFL /NDL /NP /NJH /NJS > "%REC%\ui_assets_robocopy.log" 2>&1
if errorlevel 8 (echo FAILED: UI dist copy & exit /b 1)
copy /Y "%FROZEN%\build\tools\ui\dist.tar.gz" "%BLD%\tools\ui\dist.tar.gz" > nul || (echo FAILED: UI dist.tar.gz & exit /b 1)
copy /Y "%FROZEN%\build\tools\ui\dist.tar.gz.sha256" "%BLD%\tools\ui\dist.tar.gz.sha256" > nul || (echo FAILED: UI dist.tar.gz.sha256 & exit /b 1)
copy /Y "%FROZEN%\build\tools\ui\.ui-stamp" "%BLD%\tools\ui\.ui-stamp" > nul || (echo FAILED: UI .ui-stamp & exit /b 1)
cmake --build build-r3-03b --target llama-server -j 8 > "%REC%\build_attempt2.log" 2>&1 || (echo FAILED: build - see %REC%\build_attempt2.log & exit /b 1)
:built
"%PY%" "%REPO%\tools\r3_03b_build_verify.py" --stage post --new-exe "%BLD%\bin\llama-server.exe" --r3a-exe "%R3A_EXE%" --r3a-sha %R3A_SHA% --frozen-exe "%FROZEN_EXE%" --frozen-sha %FROZEN_SHA% --record "%REC%\BUILD_IDENTITY.json"
if errorlevel 1 (echo FAILED: post-build verification - see %REC%\BUILD_IDENTITY.json & exit /b 1)
echo R3-03B BUILD COMPLETE: %BLD%\bin\llama-server.exe - identity in %REC%\BUILD_IDENTITY.json
