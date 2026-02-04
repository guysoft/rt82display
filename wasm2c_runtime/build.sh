#!/bin/bash
# Build script for QGIF native encoder
# Compiles wasm2c-generated C code into a shared library

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Detect platform
if [[ "$OSTYPE" == "darwin"* ]]; then
    SHARED_EXT="dylib"
    SHARED_FLAGS="-dynamiclib"
else
    SHARED_EXT="so"
    SHARED_FLAGS="-shared"
fi

OUTPUT="libqgif.$SHARED_EXT"

echo "Building QGIF native encoder..."
echo "Platform: $OSTYPE"
echo "Output: $OUTPUT"

# Compile with optimizations
# -fPIC: Position Independent Code (required for shared libraries)
# -O2: Optimize for speed
# -DNDEBUG: Disable assertions
# -DWASM_RT_MEMCHECK_GUARD_PAGES=0: Disable memory guard pages (simpler)

cc $SHARED_FLAGS -fPIC -O2 -DNDEBUG \
    -DWASM_RT_MEMCHECK_BOUNDS_CHECK=1 \
    -I. \
    qgif_generated.c \
    wasm-rt-impl.c \
    wasm-rt-mem-impl.c \
    wasm_syscalls.c \
    -lm \
    -o "$OUTPUT"

echo "✅ Built $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"

# Build test binary
echo ""
echo "Building test binary..."
cc -O2 -DNDEBUG \
    -DWASM_RT_MEMCHECK_BOUNDS_CHECK=1 \
    -I. \
    qgif_generated.c \
    wasm-rt-impl.c \
    wasm-rt-mem-impl.c \
    wasm_syscalls.c \
    test_qgif.c \
    -lm \
    -o test_qgif

echo "✅ Built test_qgif"

# Test if we have reference files
if [[ -f "../captured.qgif" ]]; then
    echo ""
    echo "Reference QGIF available: ../captured.qgif"
    echo "Test: python ../qgif_native.py ../test.gif /tmp/out.qgif"
fi
