#!/bin/bash
# Research 5 (ETS) runtime build: llama-server.exe and argine-ets-test.exe, cross-compiled for Windows x86_64.
#
# Same lineage as llama-research-v3..v5: llama.cpp 254b177307b14d055bbe4c050c9c441a1e06d8a4, mingw-w64 GCC
# "10-posix 20220113" (Ubuntu 22.04 jammy packages, hashes in mingw-toolchain-debs.sha256), mingw-w64 headers 8.0.0,
# -D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00, static, no OpenMP, AVX2/FMA/F16C/BMI2.
# Sources: patches 0001-0003, 0005, 0006 applied with `git apply`; 0004 (retention-policy CMake wiring) applied as its
# equivalent edit because its context predates 0002/0003; the frozen ../argine-retention-policy.{h,cpp}; the R5 copies
# in this directory of argine-store.{h,cpp}, argine-experts.cpp, argine-research.{h,cpp}, argine-ets.h and argine-tls.h.
# Nothing under ../ is modified. Needs network (github.com, archive.ubuntu.com) and root for dpkg -i
# (or unpack the debs with dpkg-deb -x into a prefix, as the v3 build did).
#
#   bash patches/r5-ets/build-r5-ets.sh <workdir>
#
# Reproducibility: __FILE__ paths are embedded, so two builds are byte-identical apart from the PE TimeDateStamp and
# CheckSum (compare with pe_normalized_sha256.py) when run with the same <workdir> path.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHES="$(cd "$HERE/.." && pwd)"
W="$(mkdir -p "$1" && cd "$1" && pwd)"
LLAMA_COMMIT=254b177307b14d055bbe4c050c9c441a1e06d8a4
UBU=http://archive.ubuntu.com/ubuntu/pool/universe
mkdir -p "$W/debs"
( cd "$W/debs"
  for u in g/gcc-mingw-w64/gcc-mingw-w64-x86-64-posix_10.3.0-14ubuntu1+24.3_amd64.deb \
           g/gcc-mingw-w64/g++-mingw-w64-x86-64-posix_10.3.0-14ubuntu1+24.3_amd64.deb \
           g/gcc-mingw-w64/gcc-mingw-w64-base_10.3.0-14ubuntu1+24.3_amd64.deb \
           g/gcc-mingw-w64/gcc-mingw-w64-x86-64-posix-runtime_10.3.0-14ubuntu1+24.3_amd64.deb \
           m/mingw-w64/mingw-w64-x86-64-dev_8.0.0-1_all.deb m/mingw-w64/mingw-w64-common_8.0.0-1_all.deb \
           b/binutils-mingw-w64/binutils-mingw-w64-x86-64_2.38-3ubuntu1+9build1_amd64.deb; do
    [ -f "$(basename "$u")" ] || curl -sSf -O "$UBU/$u"
  done
  sha256sum -c "$HERE/mingw-toolchain-debs.sha256"
  dpkg -i --force-depends ./*.deb >/dev/null )
x86_64-w64-mingw32-g++-posix --version | head -1
if [ ! -d "$W/llama.cpp/.git" ]; then
  git init -q "$W/llama.cpp"
  git -C "$W/llama.cpp" fetch -q --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_COMMIT"
  git -C "$W/llama.cpp" checkout -q FETCH_HEAD
fi
cd "$W/llama.cpp"
git reset -q --hard "$LLAMA_COMMIT" && git clean -qfd
for p in 0001-argine-route-log 0002-argine-expert-store 0003-argine-research-trace 0005-argine-prefetch-race-fix 0006-argine-msvc-atomic-shim; do
  sed 's/\r$//' "$PATCHES/$p.patch" | git apply -      # the Windows checkout stores the patches with CRLF
done
# 0004 equivalent: wire the retention-policy sources into ggml-cpu (the patch's context predates 0002/0003)
sed -i 's#^\(\s*\)ggml-cpu/argine-research.h$#&\n\1ggml-cpu/argine-retention-policy.cpp\n\1ggml-cpu/argine-retention-policy.h#' ggml/src/ggml-cpu/CMakeLists.txt
grep -q "argine-retention-policy.cpp" ggml/src/ggml-cpu/CMakeLists.txt
D=ggml/src/ggml-cpu
cp "$PATCHES/argine-retention-policy.cpp" "$PATCHES/argine-retention-policy.h" "$D/"
for f in argine-store.cpp argine-store.h argine-experts.cpp argine-experts.h argine-research.cpp argine-research.h argine-ets.h argine-tls.h; do
  if [ -f "$HERE/$f" ]; then cp "$HERE/$f" "$D/"; else cp "$PATCHES/$f" "$D/"; fi
done
sed -i 's/\r$//' $D/argine-*
rm -rf build-r5-ets                                  # always a clean configure and build
cmake -S . -B build-r5-ets -G Ninja -DCMAKE_TOOLCHAIN_FILE="$HERE/toolchain-mingw-w64.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DGGML_NATIVE=OFF \
  -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_BMI2=ON -DGGML_OPENMP=OFF \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  "-DCMAKE_C_FLAGS=-D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00" "-DCMAKE_CXX_FLAGS=-D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00" \
  "-DCMAKE_EXE_LINKER_FLAGS=-static -static-libgcc -static-libstdc++"
cmake --build build-r5-ets --target llama-server -j"$(nproc)"
mkdir -p "$W/out"
cp build-r5-ets/bin/llama-server.exe "$W/out/"
( cd "$D" && x86_64-w64-mingw32-g++-posix -O2 -std=c++17 -D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00 -I. \
    argine-store.cpp argine-research.cpp argine-retention-policy.cpp "$HERE/argine-ets-test.cpp" \
    -static -static-libgcc -static-libstdc++ -o "$W/out/argine-ets-test.exe" )
sha256sum "$W/out/"*.exe
python3 "$HERE/pe_normalized_sha256.py" "$W/out/"*.exe
