#!/usr/bin/env sh
# Builds the registry contract with the fee-optimized pipeline:
#   opt-level=z + fat LTO + codegen-units=1 + panic=abort (Cargo.toml [profile.release])
#   -> wasm-strip -> wasm-opt -Oz
# NEAR charges deployment gas per byte of contract code; every byte saved is
# a direct fee reduction. Measured: 330 KB (default release) -> 226 KB.
set -eu
cd "$(dirname "$0")/.."

cargo build -p unam-registry --target wasm32-unknown-unknown --release

OUT=dist
mkdir -p "$OUT"
cp target/wasm32-unknown-unknown/release/unam_registry.wasm "$OUT/unam_registry.wasm"
wasm-strip "$OUT/unam_registry.wasm"
if command -v wasm-opt >/dev/null 2>&1; then
    wasm-opt -Oz --enable-bulk-memory --strip-debug -o "$OUT/unam_registry.opt.wasm" "$OUT/unam_registry.wasm"
    mv "$OUT/unam_registry.opt.wasm" "$OUT/unam_registry.wasm"
fi
ls -l "$OUT/unam_registry.wasm"
echo "deploy with: near deploy <account-id> $OUT/unam_registry.wasm"
