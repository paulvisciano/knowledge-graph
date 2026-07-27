#!/usr/bin/env bash
set -euo pipefail

# Build the PrismML-Eng llama.cpp fork from source with Metal GPU support.
#
# Bonsai-27B uses custom Q1_0_g128 packed 1-bit weights with hybrid-attention
# kernels that are NOT in upstream llama.cpp. Stock llama.cpp (Homebrew or
# ggml-org/master) will fail to load Bonsai GGUFs. This script clones the
# PrismML-Eng fork (default branch: `prism`), which carries the 1-bit Metal
# kernels, and builds with GGML_METAL=ON into vendor/llama.cpp/.
#
# The Homebrew bottle of llama.cpp also ships WITHOUT Metal support — the
# model would run on CPU at 4-7 tok/s instead of 20-40 tok/s on Apple Silicon.
#
# The start-llama-servers.sh script will prefer this build over the Homebrew
# binary if it exists.  If the build is missing, it falls back to Homebrew
# (which will NOT be able to load Bonsai — re-run this script first).
#
# Usage:
#   ./scripts/build-llama-cpp.sh          # clone + build
#   ./scripts/build-llama-cpp.sh --update  # pull latest + rebuild
#
# Prerequisites:
#   - Xcode Command Line Tools:  xcode-select --install
#   - CMake:                      brew install cmake

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="$PROJECT_DIR/vendor/llama.cpp"
LLAMA_SRC="$VENDOR_DIR/src"
LLAMA_BUILD="$LLAMA_SRC/build"

LLAMA_REPO="https://github.com/PrismML-Eng/llama.cpp.git"
LLAMA_BRANCH="prism"

# Some CommandLineTools installs have a broken libc++ discovery — clang can't
# auto-find <array> etc.  Pass the SDK include path explicitly.
SDK_SYSROOT="$(xcrun --show-sdk-path 2>/dev/null || echo '')"
CXX_INCLUDE=""
if [[ -n "$SDK_SYSROOT" && -d "$SDK_SYSROOT/usr/include/c++/v1" ]]; then
    CXX_INCLUDE="$SDK_SYSROOT/usr/include/c++/v1"
fi

echo "=== Building llama.cpp with Metal support ==="
echo "SDK sysroot: $SDK_SYSROOT"
echo "C++ include: ${CXX_INCLUDE:-<auto>}"
echo ""

# Clone or update
if [[ "${1:-}" == "--update" && -d "$LLAMA_SRC/.git" ]]; then
    echo "Updating existing clone..."
    cd "$LLAMA_SRC"
    git fetch origin "$LLAMA_BRANCH"
    git reset --hard "origin/$LLAMA_BRANCH"
    rm -rf "$LLAMA_BUILD"
else
    if [[ -d "$LLAMA_SRC" ]]; then
        echo "Source already exists at $LLAMA_SRC (use --update to refresh)"
    else
        echo "Cloning llama.cpp..."
        mkdir -p "$VENDOR_DIR"
        git clone --depth 1 -b "$LLAMA_BRANCH" "$LLAMA_REPO" "$LLAMA_SRC"
    fi
fi

# Configure
echo ""
echo "Configuring..."
CMAKE_CXX_FLAGS=""
CMAKE_C_FLAGS=""
if [[ -n "$CXX_INCLUDE" ]]; then
    CMAKE_CXX_FLAGS="-I$CXX_INCLUDE"
fi
if [[ -n "$SDK_SYSROOT" ]]; then
    CMAKE_CXX_FLAGS="$CMAKE_CXX_FLAGS -isysroot $SDK_SYSROOT"
    CMAKE_C_FLAGS="-isysroot $SDK_SYSROOT"
fi

cmake -S "$LLAMA_SRC" -B "$LLAMA_BUILD" \
    -DGGML_METAL=ON \
    -DGGML_METAL_EMBED_LIBRARY=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_FLAGS="$CMAKE_CXX_FLAGS" \
    -DCMAKE_C_FLAGS="$CMAKE_C_FLAGS" \
    -DCMAKE_OSX_SYSROOT="$SDK_SYSROOT"

# Build
echo ""
echo "Building (this takes a few minutes)..."
cmake --build "$LLAMA_BUILD" --config Release -j "$(sysctl -n hw.ncpu 2>/dev/null || echo 8)"

# Verify Metal is linked
echo ""
echo "=== Verifying Metal support ==="
if otool -L "$LLAMA_BUILD/bin/llama-server" | grep -q "libggml-metal"; then
    echo "OK: libggml-metal.dylib is linked"
else
    echo "WARNING: libggml-metal not found in build output — Metal may not work"
    echo "Check the cmake output above for GGML_METAL=ON"
fi

echo ""
echo "=== Build complete ==="
echo "Binary: $LLAMA_BUILD/bin/llama-server"
echo "$LLAMA_BUILD/bin/llama-server --version"
"$LLAMA_BUILD/bin/llama-server" --version
echo ""
echo "The start-llama-servers.sh script will now use this build automatically."