#!/bin/bash
# Research 5 Phase 1a: reconstruction of llama-research-v5 (R2-12 commit 34997c2) from frozen inputs.
#
# Recipe = tools/build_llama_research.sh at 34997c2 (its /tmp/mingw.cmake toolchain file and FLAGS; the v3..v5 builds invoked the
#          same compiler from a user-owned prefix, which this script does not reproduce)
#        + the documented v3..v5 deviation -D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00 (C and C++ flags)
#        + patches/README.md order: 0002, copy argine-{store,experts,research}.{h,cpp}, 0001, 0003
#        + 0004 (retention-policy CMake wiring), realised as the edit whose result must equal 0004's post-image blob 8f9d51d
#        + argine-retention-policy.{h,cpp} (v3..v5 BUILD-INFO).
# Sources: a `git archive 34997c2 patches` export (LF), checked against tree 03fe2f75 by the caller.
# Toolchain: Ubuntu 22.04 mingw-w64 GCC 10.3.0-posix ("10-posix 20220113"), mingw-w64 8.0.0, binutils 2.38 (pinned debs);
# CMake 3.22.1 and Ninja 1.10.1 from the Ubuntu 22.04 debs, unpacked into a prefix (the v3 build obtained cmake/ninja the
# same way; their exact versions were not recorded).
# Built at /home/claude/llama.cpp, the source path the historical Argine builds embed in __FILE__ strings.
#
#   bash build-v5-recovery.sh <SRC (export dir containing patches/)> <TC prefix> <PRISTINE llama.cpp git> <OUT dir>
set -euo pipefail
SRC="$1"; TC="$2"; PRISTINE="$3"; OUT="$4"
PIN=254b177307b14d055bbe4c050c9c441a1e06d8a4
W=/home/claude/llama.cpp
P="$SRC/patches"
[ ! -e "$W" ] || { echo "refusing: $W exists (clean build required)"; exit 1; }
export PATH="$TC/usr/bin:$PATH"
cmake --version | head -1; ninja --version; x86_64-w64-mingw32-g++-posix --version | head -1
git clone -q -b pin "$PRISTINE" "$W"
cd "$W"
[ "$(git rev-parse HEAD)" = "$PIN" ]
git apply "$P/0002-argine-expert-store.patch"
cp "$P"/argine-{store,experts,research}.{h,cpp} ggml/src/ggml-cpu/
git apply "$P/0001-argine-route-log.patch"
git apply "$P/0003-argine-research-trace.patch"
# 0004: its pre-image is the pristine CMakeLists (71f55b4), so after 0002/0003 it no longer applies textually;
# apply the same edit and require the exact post-image blob.
sed -i 's#^\(\s*\)ggml-cpu/argine-research.h$#&\n\1ggml-cpu/argine-retention-policy.cpp\n\1ggml-cpu/argine-retention-policy.h#' ggml/src/ggml-cpu/CMakeLists.txt
case "$(git hash-object ggml/src/ggml-cpu/CMakeLists.txt)" in 8f9d51d*) echo "0004 post-image blob verified";; *) echo "0004 post-image mismatch"; exit 1;; esac
cp "$P"/argine-retention-policy.{h,cpp} ggml/src/ggml-cpu/
cat > /tmp/mingw.cmake <<'TC'
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_C_COMPILER x86_64-w64-mingw32-gcc-posix)
set(CMAKE_CXX_COMPILER x86_64-w64-mingw32-g++-posix)
set(CMAKE_RC_COMPILER x86_64-w64-mingw32-windres)
set(CMAKE_FIND_ROOT_PATH /usr/x86_64-w64-mingw32)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
TC
FLAGS=(-DCMAKE_TOOLCHAIN_FILE=/tmp/mingw.cmake -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
       -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON
       -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_BMI2=ON -DGGML_OPENMP=OFF
       -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
       "-DCMAKE_EXE_LINKER_FLAGS=-static -static-libgcc -static-libstdc++"
       "-DCMAKE_C_FLAGS=-D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00" "-DCMAKE_CXX_FLAGS=-D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00")
cmake -B build-win -G Ninja "${FLAGS[@]}" > "$OUT.cmake.log"
ninja -C build-win llama-server llama-perplexity llama-quantize llama-imatrix > "$OUT.ninja.log"
# The server embeds a web UI only if assets are available (tools/ui/dist or a HuggingFace download); the reconstruction
# contract is "no embedded UI" (the G1 probe uses only /health and /completion): fail if any asset was embedded.
grep -q -- "-- UI: embedded 0 assets" "$OUT.ninja.log" || { echo "web UI assets were embedded: not the reconstruction contract"; exit 1; }
mkdir -p "$OUT"
cp build-win/bin/llama-{server,perplexity,quantize,imatrix}.exe "$OUT/"
cp build-win/compile_commands.json "$OUT/" 2>/dev/null || true
git diff --stat HEAD > "$OUT/llama-tree.diffstat"; git status --porcelain > "$OUT/llama-tree.status"
( cd "$OUT" && sha256sum *.exe )
