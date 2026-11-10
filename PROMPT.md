# Build Prompt: Universal Name — NEAR Registry Contract + Optional Routing Layers

You are starting implementation of **Universal Name** (working suffix `.uni`, examples use `peni.uni`), a payment-name registry on NEAR. Read `Universal Name One Name, Any Chain, Any Currency, Optional Bank Payout.md` in this repository first — it is the authoritative spec. This prompt turns the spec into an ordered build plan with acceptance criteria. Today is Nov 9, 2026; the build window closes **Nov 14, 2026 23:59 UTC** and the internal target is **Nov 14 12:00 UTC**. Budget accordingly: the registry contract and core resolver flow are the must-have; the two optional layers are only built if the core is done and tested.

## Non-negotiables (from the spec)

- One registry contract in Rust (`near-sdk-rs`) on NEAR testnet. No contracts on any other chain.
- Every chain record stores an **owner-signed proof of control** next to the address. A record without a valid proof must never be trusted by the resolver.
- Address values are stored and returned as **ERC-7930 Interoperable Addresses**; record keys are CAIP-2 chain IDs (`eip155:1`, `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d`, `bip122:000000000019d6689c085ae165831e93`). Keep an ENSIP-11 coinType index (`0x80000000 | chainId` for EVM) alongside for legacy-tooling compatibility.
- Receive policy is shaped so it maps onto **ENSIP-31 Payment Preferences**: accepted asset + preferred destination per asset.
- Honest failures: an unconnected chain resolves to nothing (`NO_RECORD`); an unsupported token on a routed record resolves to `NO_ROUTE` with the supported list. Never guess an address.
- Payout/policy changes take effect only after a **time delay** and emit a notification event. No bank details on-chain — only an opaque provider reference.
- Out of scope: ICANN TLD registration, native wallet resolution of `.uni`, mobile app, multi-provider off-ramp, governance/token.

## Phase 1 — Registry contract (must-have; build this first)

### 1.1 Scaffold

- New Rust crate `contracts/registry` using `near-sdk-rs` (latest stable), `cargo` build with `wasm32v1-none` target, and `near-workspaces-rs` (or `near-sandbox` via workspaces) for integration tests.
- Add `cargo fmt`, `cargo clippy -D warnings`, and `cargo test` to CI (a GitHub Actions workflow is enough; no over-engineering).
- Deploy target: NEAR **testnet**. Provide a deploy script (shell or Rust binary) that builds the wasm, creates a sub-account (e.g. `uni-registry.<you>.testnet`), deploys, and initializes. Also produce a devnet/sandbox test harness that does not require real testnet NEAR for the unit suite.

### 1.2 Data model

One contract-level map from `name → Record`. `Record`:

- `name: String` — normalized name (lowercase, ASCII or ENSIP-15-style normalization; enforce a reserved-name list and confusable-character rejection — implement the checks in code, cite ENSIP-15 as the reference behavior).
- `owner: AccountId` — NEAR account that controls the record.
- `records: HashMap<ChainKey, ChainRecord>` where `ChainKey` is the CAIP-2 string (plus a derived `coinType: u64` index) and `ChainRecord` holds:
  - `address: Vec<u8>` — raw address bytes in the chain's native binary encoding (20 bytes EVM, 32 bytes Solana ed25519, Bitcoin scriptPubKey encoding per ENSIP-9). Store the full ERC-7930 envelope here or keep the envelope construction in the resolver service — pick one, document the choice, and be consistent. Recommendation: store the native bytes on-chain, build the 7930 envelope in the resolver/SDK (cheaper storage, envelope is derivable).
  - `proof: Vec<u8>` — signature from that wallet over a challenge binding the name, the chain, and the address (domain-separated, nonce included, expiry included; NEP-413-style for NEAR wallets).
  - `set_at: u64` — block timestamp, for staleness display.
- `receive_policy: Option<ReceivePolicy>`:
  - `default: Option<Destination>` — `{ asset: AssetId, address: Vec<u8>, chain: ChainKey }`, where `AssetId` is a CAIP-19-style asset id (e.g. `solana:.../USDC` or `eip155:1/erc20:0x...`).
  - `payout: Option<PayoutRef>` — `{ provider: String, ref: String }` opaque reference only.
  - `force_default: bool` — see spec open question 7; default `false`.
- `pending_change: Option<PendingChange>` — `{ effective_at: u64, change: PendingChangeKind }` where the change kind covers payout destination and default-asset changes (address record changes may also be delayed — implement delay for policy/payout first, keep record changes instant, matching the spec's threat table).
- `version: u32` — schema version, start at 1.

Storage fees: use `near-sdk`'s `UnorderedMap`/`TreeMap` primitives as appropriate and charge storage staking normally. Keep per-record storage small (no bank data, no PII).

### 1.3 Interface

Write-path (owner or preapproved operator only):

- `claim(name: String)` — register an unused name; owner = `predecessor_account_id`. Emit `NameClaimed { name, owner }`.
- `connect_chain(name, chain: ChainKey, address: Vec<u8>, proof: Vec<u8>)` — attach/replace a chain record. Emit `ChainConnected { name, chain, address }`. The contract stores the proof; **signature verification is the resolver service's job** for EVM/Solana (secp256k1/ecrecover and ed25519 verification are impractical on NEAR runtime). For NEAR (`nep141:`/NEAR chain key), verify NEP-413 semantics where feasible on-chain, otherwise same treatment — document the trust boundary in the contract's rustdoc.
- `disconnect_chain(name, chain)`.
- `set_receive_policy(name, policy: Option<ReceivePolicy>)` — creates a `pending_change` with the configured delay instead of applying immediately. Emit `PolicyChangeQueued { name, effective_at }`.
- `apply_pending_change(name)` — anyone may call after `effective_at`; applies and emits `PolicyChanged`.
- `cancel_pending_change(name)` — owner only.
- `transfer_name(name, new_owner)` — clears `pending_change`, keeps records but marks all proofs stale (bump a `generation` counter so the resolver treats pre-transfer proofs as stale, mirroring SNS Records V2 staleness).

Read-path (view calls — the resolver service and SDK hit these):

- `resolve_direct(name, chain) -> Option<ChainRecord>` — the core call. `None` means `NO_RECORD`.
- `get_policy(name) -> Option<ReceivePolicy>`.
- `get_pending_change(name) -> Option<PendingChange>`.
- `owner_of(name)`, `records_of(owner)` (paginated), `is_name_available(name)`.
- `supported_routes(chain, token) -> Option<RouteInfo>` — see Phase 2; return `NO_ROUTE` semantics when absent.

Errors as typed enums: `NameTaken`, `NameNotVerified` (reserved/normalization rejection), `NotOwner`, `NoRecord`, `NoPendingChange`, `ChangePending { effective_at }`, `InvalidChainKey`, `InvalidAddressForChain` (length/format checks per chain: 20-byte EVM, 32-byte Solana, Bitcoin script encodings per ENSIP-9).

### 1.4 Security requirements (mirror the spec's threat table)

- Owner-signed changes only; delayed application for policy/payout; every change emits events for owner notification.
- No bank details on-chain — `PayoutRef` carries an opaque provider reference.
- Rate-limit nothing on-chain (that is the resolver service's job), but keep events rich enough for the service to index and to cache.
- Multicall-free, reentrancy-free (NEAR is single-threaded per contract call, but document any cross-contract calls explicitly).

### 1.5 Tests (acceptance criteria for Phase 1)

- Claim, connect three chains (NEAR, `eip155:1`, Solana mainnet key), resolve each, resolve an unconnected chain → `None`.
- Proof storage and generation counter: transfer name → old proofs stale.
- Policy change: queue → cannot resolve as active before delay → apply after delay → active; cancel path works.
- Malformed chain keys and wrong-length addresses rejected with typed errors.
- Sandbox integration test deploys the contract and runs the full vertical-slice happy path.
- `cargo test` green, `cargo clippy` clean, wasm builds.

## Phase 2 — Router adapter (optional; only after Phase 1 is green)

Purpose: implement routed mode — `resolve` returns a **persistent deposit address** that converts and delivers automatically. This is optional; the core product works without it.

- New Rust crate `services/router` (an Axum service is fine; it is off-chain, no contract).
- Primary path: **Aurora Intents Persistent Addresses** — permanent, reusable deposit address tied to source chain + recipient + optional output asset. Creating addresses is **gated**: non-approved API keys get `403`. Send the approval request on day one (see spec open questions); until approved, this crate compiles and runs against a mocked adapter behind a trait.
- Fallback within Phase 2: **NEAR Intents 1Click** — supported-chain list (EVM, Solana, NEAR, TON, Stellar, Tron), dry quote (pricing only) vs live quote (single-use deposit address). 1Click live quotes return single-use addresses, which breaks the "resolve before amount is known" flow — this is documented in the spec; use 1Click only for the sweep leg of Phase 3, not as the persistent deposit address.
- Behavior: implement the spec's resolution order exactly — `NAME_NOT_FOUND` → `force_default` route → connected wallet → default policy route → `NO_RECORD`/`NO_ROUTE`. Routed responses must carry: deposit address, supported-asset warnings, `min_amount`, `est_receive`, `expires_at`, and a **resolver signature over the response body** (ed25519 key held by the service; SDK verifies against a published key).
- Status tracking: poll the provider until a terminal state (`success | failed | refunded`); record transitions for the owner's dashboard; on failure, surface the refund path.
- Config: no secrets in git; provider keys via env. Fee parameters (Aurora 60/40 split, 2 bps stablecoin floor, 10 bps other, 500 bps cap) read from config, never hard-coded.
- Tests: unit tests for the resolution-order state machine with a mock adapter; golden tests for response signing/verification.

## Phase 3 — Chain Signatures wallet manager (fallback; only if Aurora approval has not arrived by Nov 11)

Purpose: fill the `NO_RECORD` gap and provide reusable deposit addresses without Aurora — derived wallets on other chains controlled by the registry's NEAR account.

- Extends the registry contract (a `derive_deposit_address(name, chain)` view + sweep logic) plus a Rust service crate `services/cs-manager`.
- Derivation: deterministic address per `(registry contract account, name, chain)` via the documented derivation-path scheme; same inputs always give the same address; no other NEAR account can control it. Signer contract: `v1.signer-prod.testnet` on testnet, `v1.signer` on mainnet.
- Support: ECDSA (secp256k1 — EVM, Bitcoin) and EdDSA (Ed25519 — Solana). Encode returned deposit addresses per the CASA `chainType` registry (`eip155`, `bip122`, `solana`).
- Sweep flow: watcher service detects a deposit on the target chain → contract requests an MPC signature → sweep to the owner's connected wallet, or to a NEAR Intents 1Click deposit address when a swap/bank payout is configured.
- Known constraints (must be handled, cited in the spec):
  - **Gas**: a derived address needs native gas to move funds; integrate or simulate NEAR's Multichain Gas Station and confirm fit before relying on it. For the hackathon demo, pre-funding derived addresses with gas from a faucet account is acceptable if labelled.
  - **Signature cost**: attach the documented NEAR deposit (1 yoctoNEAR up to 0.65 NEAR scaling with pending requests; unused deposit is refunded).
  - **Trust**: this path is closer to custody — enforce per-name sweep limits, an audit log of every signature request, and strict contract-side rules on who may request signatures and where funds may go.
- Tests: derivation determinism (same name+chain → same address across runs), sweep request authorization (only the watcher/service account can request; destination constrained to owner records or approved routes), deposit-amount accounting and refund of unused signature deposits in sandbox.

## Build order and daily checkpoints

1. **Nov 9–10:** Phase 1 complete on testnet (contract + tests + deploy script). In parallel: send the Aurora approval request and register the `.uni` suffix decision (open question 1) — do not block on either.
2. **Nov 11:** Phase 1 demo path works end to end from the web app (claim → connect wallets with proofs → resolve → error states). Start Phase 2 behind the adapter trait.
3. **Nov 12:** Finish Phase 2 if time allows; otherwise it is a stretch goal. Phase 3 only if Aurora is not approved and Phase 2 is done. Security review pass against the spec's threat table.
4. **Nov 13:** Tests, README (`do X, see Y` demo script from the spec), demo video.
5. **Nov 14 morning:** submission pack. Freeze.

## Definition of done for this prompt

- `contracts/registry` deploys to NEAR testnet; all tests green in CI.
- `resolve_direct` for a connected chain returns the owner's own address; an unconnected chain returns nothing; a client can render the spec's error messages from the returned data.
- Router and CS-manager crates either exist with mocked-provider tests, or a README section explains precisely why they were deferred and what the core does instead.
- No secrets committed; no bank/PII data on-chain; every write path emits events; every non-obvious trust decision documented in rustdoc.
