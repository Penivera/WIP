#!/usr/bin/env sh
# Deploys the Universal Name registry contract to NEAR testnet
# Usage:
#   ./scripts/deploy-testnet.sh [contract-account.testnet]
#
# Defaults to:
#   unam-registry.testnet

set -eu

cd "$(dirname "$0")/.."

CONTRACT_ACCOUNT="${1:-unam-registry.testnet}"

echo "==> Building optimized contract wasm..."
sh scripts/build-contract.sh

WASM_FILE="dist/unam_registry.wasm"
if [ ! -f "$WASM_FILE" ]; then
    echo "Error: $WASM_FILE not found after build"
    exit 1
fi

echo "==> Checking if account $CONTRACT_ACCOUNT exists on testnet..."
if near account view-account-summary "$CONTRACT_ACCOUNT" network-config testnet now >/dev/null 2>&1; then
    echo "Account $CONTRACT_ACCOUNT exists."
else
    echo "Creating account $CONTRACT_ACCOUNT via testnet faucet..."
    near account create-account sponsor-by-faucet-service "$CONTRACT_ACCOUNT" \
        autogenerate-new-keypair --signature-scheme ed25519 save-to-keychain \
        network-config testnet create
    echo "Account $CONTRACT_ACCOUNT created successfully."
fi

echo "==> Deploying and initializing contract at $CONTRACT_ACCOUNT..."
near contract deploy "$CONTRACT_ACCOUNT" use-file "$WASM_FILE" \
    with-init-call new json-args '{"change_delay_secs": 3600}' \
    prepaid-gas '100.0 Tgas' attached-deposit '0 NEAR' \
    network-config testnet sign-with-keychain send

echo "==> Verifying contract view methods on testnet:"
echo "--- change_delay_ms ---"
near contract call-function as-read-only "$CONTRACT_ACCOUNT" change_delay_ms json-args '{}' network-config testnet now

echo "==> Universal Name Registry successfully deployed to $CONTRACT_ACCOUNT on testnet!"
