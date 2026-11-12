# Universal Name (.uni) — Resolver & Intent Router API

Backend orchestration service for Universal Name (`.uni`). Queries `unam-registry.testnet` on NEAR Testnet, returns canonical **ERC-7930 Interoperable Addresses**, enforces **ENSIP-31 Payment Preferences**, and executes live cross-chain routing via **NEAR Intents 1Click API** and **NEAR Chain Signatures** (`v1.signer-prod.testnet`).

## Quickstart

1. Install [uv](https://docs.astral.sh/uv/)
2. Install dependencies:
   ```bash
   cd services/resolver-py
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

## Endpoints

- `GET /v1/resolve?name=demo.uni&chain=eip155:1&token=native` — Resolves a `.uni` name (Mode 1 Direct, Mode 2 Routed via 1Click/Chain Signatures, or Mode 3 Bank Payout).
- `GET /v1/tokens` — Returns supported tokens from 1Click API (`/v0/tokens`).
- `POST /v1/deposit/submit` — Submits an on-chain deposit `txHash` and `depositAddress` to 1Click (`/v0/deposit/submit`).
- `GET /v1/status?depositAddress=...` — Polls live swap status from 1Click (`/v0/status`) and updates the recipient dashboard.
- `GET /v1/cs/derive?name=peni.uni&chain=eip155:1` — Deterministically derives a NEAR Chain Signatures deposit address.
- `GET /v1/dashboard/{name}` — Returns tracked incoming payments and MPC sweep audit logs for `{name}`.
- `POST /v1/demo/transition` — Simulates terminal payment states (`SUCCESS`, `REFUNDED`, `OFFRAMP_REJECT`) for demo flows.
