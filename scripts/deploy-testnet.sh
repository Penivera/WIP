#!/usr/bin/env sh
# Deploys the Universal Name registry contract to NEAR testnet
# Usage:
#   ./scripts/deploy-testnet.sh <your-account.testnet> [contract-account.testnet]
#
# If contract-account is omitted, it defaults to:
#   uni-registry.<your-account.testnet>

set -eu

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
    echo "Usage: $0 <master-account.testnet> [contract-account.testnet]"
    echo "Example: $0 alice.testnet uni-registry.alice.testnet"
    exit 1
fi

MASTER_ACCOUNT="$1"
CONTRACT_ACCOUNT="${2:-uni-registry.$MASTER_ACCOUNT}"

echo "==> Building optimized contract wasm..."
sh scripts/build-contract.sh

WASM_FILE="dist/unam_registry.wasm"
if [ ! -f "$WASM_FILE" ]; then
    echo "Error: $WASM_FILE not found after build"
    exit 1
fi

echo "==> Deploying to $CONTRACT_ACCOUNT (signer: $MASTER_ACCOUNT)..."
echo "If creating subaccount is needed:"
echo "  near account create-account fund-myself $CONTRACT_ACCOUNT '5 NEAR' autogenerate-new-keypair save-to-keychain sign-as $MASTER_ACCOUNT network-config testnet"
echo ""
echo "Deploying and initializing contract:"
near contract deploy "$CONTRACT_ACCOUNT" use-file "$WASM_FILE" \
    with-init-call new json-args '{"change_delay_secs": 3600}' prepaid-gas '100.0 Tgas' attached-deposit '0 NEAR' \
    sign-as "$CONTRACT_ACCOUNT" network-config testnet

echo "==> Verifying deployment:"
near contract call-function as-read-only "$CONTRACT_ACCOUNT" change_delay_ms json-args '{}' network-config testnet
echo "==> Deployment complete!"
