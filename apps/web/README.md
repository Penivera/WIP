# Universal Name web widget

The pay and "my names" screens for Universal Name, built as a popup that can be embedded in any page. It needs the Python resolver (`resolver-py`) running for token lists, payment details and payment status. It reads the registry contract directly from NEAR.

## What you need

- Node 22 and [pnpm](https://pnpm.io) 9
- [uv](https://docs.astral.sh/uv/) for the Python resolver (it installs Python 3.13 for you)

## 1. Start the resolver

From the repository root:

```bash
cd resolver-py
uv sync
uv run python main.py
```

It serves on http://localhost:8000.

By default it reads the live contract, `unam-registry.testnet`. To try the screens with built-in sample names instead (`peni`, `demo`, `routed`, `bank`):

```bash
cp .env.example .env
# set USE_MOCK_CONTRACT="true" in .env, then restart the resolver
```

Do not send real funds to the sample names. Their addresses are examples, not wallets anyone on the team controls.

## 2. Start the web app

In a second terminal, from the repository root:

```bash
pnpm install
pnpm dev:web
```

Open the address Vite prints (http://localhost:5173 unless that port is taken). The page is a stand-in for a host site, with the popup open on top of it.

- `?name=peni` pre-fills the name on the pay card.
- `?start=names` opens on the owner's cards.

## Settings

The defaults work for local development. To change them, copy `.env.example` to `.env.local` in `apps/web`:

| Variable | Default | What it is |
|---|---|---|
| `VITE_API_URL` | `http://localhost:8000` | The Python resolver |
| `VITE_CONTRACT_ID` | `unam-registry.testnet` | Registry contract |
| `VITE_NEAR_NETWORK` | `testnet` | NEAR network for the owner's wallet |
| `VITE_NEAR_RPC_URL` | `https://rpc.testnet.near.org` | NEAR RPC used to read the contract |
| `VITE_NAME_SUFFIX` | `.uni` | Suffix stripped from names |

Everything here is shipped to the browser, so put no secrets in it.

## Embedding it in another site

Build the single-file version:

```bash
pnpm build:embed
```

This writes `apps/web/dist-embed/universal-name.js`. On the host page:

```html
<script src="universal-name.js"></script>
<script>
  UniversalName.open({ name: "peni" });
</script>
```

`open` options, all optional:

| Option | Meaning |
|---|---|
| `name` | Name to pre-fill on the pay card |
| `start` | `"pay"` (default) or `"names"` |
| `backdrop` | `false` to leave the page behind undimmed |
| `onClose` | Called after the popup closes |
| `apiUrl`, `contractId`, `network`, `rpcUrl`, `suffix` | Override the settings above |

`UniversalName.close()` closes it.

## Other commands

Run from the repository root:

| Command | What it does |
|---|---|
| `pnpm typecheck` | Type-checks the web app and the shared package |
| `pnpm test` | Runs the shared package's tests |
| `pnpm build:web` | Builds the demo page into `apps/web/dist` |

## Things to know

- The contract is on NEAR testnet, but routed payments go through NEAR Intents on mainnet with real funds.
- A routed payment address is for one payment only. The pay card gives 20 minutes to send, then asks for new details.
- Bank payout is simulated.
- The resolver keeps payment history in memory, so "Incoming payments" empties when it restarts.
- Claiming a name from the widget does not work on the live contract yet: the contract now requires payment to claim, and the Claim button has not been connected to a payment route.
