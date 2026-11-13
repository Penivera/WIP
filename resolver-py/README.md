# Universal Name (.uni) — Resolver & Intent Router API

Backend orchestration service for Universal Name (`.uni`). Queries `unam-registry.testnet` on NEAR Testnet, returns canonical **ERC-7930 Interoperable Addresses**, enforces **ENSIP-31 Payment Preferences**, and executes live cross-chain routing via **NEAR Intents 1Click API** and **NEAR Chain Signatures** (`v1.signer-prod.testnet`).

## Quickstart

1. Install [uv](https://docs.astral.sh/uv/)
2. Install dependencies:
   ```bash
   cd resolver-py
   uv sync
   ```
3. (Optional) Copy `.env.example` to `.env` to customize settings:
   ```bash
   cp .env.example .env
   ```
   *Note: By default, `USE_MOCK_CONTRACT="false"` queries the live `unam-registry.testnet` contract. Set `USE_MOCK_CONTRACT="true"` in `.env` if you want to test frontend UI flows against pre-seeded mock names (`demo.uni`, `peni.uni`, `routed.uni`, `bank.uni`) before claiming them on-chain.*
4. Run unit tests:
   ```bash
   uv run pytest -v
   ```
5. Start the FastAPI server (`http://localhost:8000`):
   ```bash
   uv run python main.py
   ```

*See [DOCS_SUPPORTED_STABLES.md](../DOCS_SUPPORTED_STABLES.md) for full details on multichain stablecoin support, intent settlement, and cross-chain routing.*

## Endpoints

### Domain Registration & Multichain Stablecoin Checkout
- `GET /v1/checkout/pricing?name={name}` — Returns length-tiered pricing ($50 / $20 / $5), availability status, and supported payment channels.
- `GET /v1/checkout/chains` — Lists all supported blockchains (Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, Avalanche, Solana, NEAR) and accepted stablecoins (USDC, USDT).
- `POST /v1/checkout/session` — Creates a checkout session with a deposit address on the buyer's chain.
- `GET /v1/checkout/session/{checkout_id}` — Retrieves live payment status (`PENDING_PAYMENT`, `PROCESSING`, `COMPLETED`).
- `POST /v1/checkout/submit` — Submits origin chain payment transaction hash and triggers automated NEAR contract fulfillment (`claim_for`).
- `POST /v1/checkout/simulate` — Simulates terminal payment transitions for integration testing.

### Name Resolution & Routing
- `GET /v1/resolve?name=demo.uni&chain=eip155:1&token=native` — Resolves a `.uni` name (Mode 1 Direct, Mode 2 Routed via 1Click/Chain Signatures, or Mode 3 Bank Payout).
- `GET /v1/tokens` — Returns supported tokens from 1Click API (`/v0/tokens`).
- `POST /v1/deposit/submit` — Submits an on-chain deposit `txHash` and `depositAddress` to 1Click (`/v0/deposit/submit`).
- `GET /v1/status?depositAddress=...` — Polls live swap status from 1Click (`/v0/status`) and updates the recipient dashboard.
- `GET /v1/cs/derive?name=peni.uni&chain=eip155:1` — Deterministically derives a NEAR Chain Signatures deposit address.
- `GET /v1/dashboard/{name}` — Returns tracked incoming payments and MPC sweep audit logs for `{name}`.
- `POST /v1/demo/transition` — Simulates terminal payment states (`SUCCESS`, `REFUNDED`, `OFFRAMP_REJECT`) for demo flows.

## License

This project is licensed under the **GNU General Public License Version 2** (`GPL-2.0-only`). See [LICENSE](file:///home/peni/Projects/wip/LICENSE).
