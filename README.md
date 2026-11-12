# Universal Name: One Name, Any Chain, Any Currency, Optional Bank Payout

Concept and technical specification for NEAR HACKATHON 2026 · Nov 7, 2026 · @Peni

*Working title for the name suffix is `.uni`; examples use `peni.uni`. See Open Questions.*

## Deployment & Quick Links

- **Live Testnet Contract:** [`unam-registry.testnet`](https://explorer.testnet.near.org/accounts/unam-registry.testnet)
- **Multichain Stablecoin & Intent Settlement Guide:** [DOCS_SUPPORTED_STABLES.md](file:///home/peni/Projects/wip/DOCS_SUPPORTED_STABLES.md)
- **Backend Resolver & Router Service:** [resolver-py/README.md](file:///home/peni/Projects/wip/resolver-py/README.md)

## Standards alignment

The registry is designed around two emerging standards:

- **ERC-7930 Interoperable Addresses** (with CAIP-350 per-namespace profiles): the binary envelope that binds a target address to its chain. Address records in the registry use this format (`version | chainType | chainRef | addrLen | addr`) instead of a `uint256 coinType` key, so any chain type — EVM, Solana, Bitcoin, or future namespaces — is supported and the resolved value is a canonical Interoperable Address.
- **ENSIP-31 Payment Preferences**: the name publishes accepted assets, preferred chains per asset and chain identifiers (EIP-7930 chain IDs / `7930 Chain Identifiers` for native assets). The receive policy in the registry is laid out so it maps onto an ENSIP-31 record, letting any client that implements the standard discover the owner's preferred settlement chain for a given asset without a custom lookup.

These are standards, not a product differentiator — existing ENS-based projects already build receive preferences. The differentiator stays NEAR-native: a registry contract on NEAR, one verified wallet per chain with proof of control, and settlement routed through NEAR Intents (Aurora Persistent Addresses) or Chain Signatures, with optional bank payout. The standards simply make the registry interoperable with whoever implements them.

## Summary

One name that resolves to the owner's own wallet on every chain they have connected. A sender types the name and the app returns the owner's address for the chain and token being sent. If the owner has not connected a wallet for that chain, the resolver returns nothing and the client shows a clear error instead of guessing. An optional receive mode lets the owner be paid in a different asset, or in a bank account, through Aurora Intents.

The core product is the resolution call `resolve(name, chain, token)`. Direct mode returns the owner's own address for that chain as a canonical **ERC-7930 Interoperable Address**; the sender just transfers to it. Routed mode is optional: it returns an Aurora Intents (or Chain Signatures fallback) deposit address that converts and delivers automatically, honoring an **ENSIP-31 Payment Preferences** receive policy — the owner's preferred asset/chain for the token being sent. The sender's wallet does an ordinary transfer either way.

## The problem

- Addresses are chain-specific. Senders must know the right chain, token and address, and mistakes are irreversible.
- A freelancer or merchant paid from several chains must run several wallets, swap or bridge, then off-ramp by hand to reach a bank.
- Existing naming systems already map one name to many addresses (see Prior Art), but funds arrive as whatever the sender sent, on the chain they sent it. The naming systems themselves do not honour a receiving preference. Apps built on top of them now do (see Prior Art), mostly through payment links rather than a normal wallet send, and bank settlement lives in separate licensed products.

The core of this design is one name with one verified wallet per chain. The optional **receive policy** lets the owner ask for a different asset or a bank payout. Receiver-chosen settlement is no longer new (see Prior Art), so it is a feature, not the pitch.

## How it works

| Mode | Owner configures | Sender sends | Recipient gets | Mechanism |
| --- | --- | --- | --- | --- |
| 1. Direct (core) | Connects a wallet for each chain, with a signed proof of control | ETH on Ethereum | ETH at the owner's own Ethereum wallet | Registry record lookup, same idea as ENS multichain records |
| 2. Preferred asset (optional) | Default receive: USDC on Solana | ETH on Ethereum | USDC on Solana | Resolver returns an Aurora Intents persistent deposit address (fallback: a Chain Signatures wallet plus a NEAR Intents swap) |
| 3. Bank (optional) | Default receive: bank account | Any supported asset | Fiat in the bank account | Swap to USDC on Solana, then a licensed off-ramp provider converts and pays out |

**Resolution order for `resolve(name, chain, token)`:**

1. Unknown name: return `NAME_NOT_FOUND`.
2. If the owner set `force_default` and a supported route exists, return the routed address (optional mode).
3. Otherwise, if the owner connected a wallet for this chain, return that address (core mode).
4. Otherwise, if a default receive policy exists and a supported route exists, return the routed address (optional mode).
5. Otherwise return `NO_RECORD`. The client shows an error such as "peni.uni has no Solana wallet connected" and blocks the send.

The final step matters: the main way users lose funds in this design is sending an unsupported token to a deposit address, so the resolver only answers for pairs the router currently supports. Aurora's own UX guidance says a deposit address should always be shown with its network, supported assets and minimum, which a plain wallet send cannot show, so the resolver and SDK must carry those warnings.

## User flows

**A. Register and configure**

1. Owner connects a NEAR wallet and claims `peni.uni`.
2. Owner connects a wallet for each chain they want to receive on, and signs a challenge from that wallet to prove control. A chain with no connected wallet returns nothing.
3. Optional: owner sets a default receive asset and a bank payout. For bank payout, the owner completes verification with the off-ramp provider; the registry stores only a provider reference, never bank details.
4. Changes to addresses and payout destination take effect after a delay and notify the owner (see Security).

**B. Send (wallet-native or SDK)**

1. Sender enters `peni.uni` in the send field of an integrated wallet or app.
2. The app calls the resolver with the sender's chain and token.
3. If the owner has no wallet for that chain, the resolver returns `NO_RECORD` and the app shows an error and blocks the send.
4. Otherwise the app shows the resolved address plus a plain-language summary. In routed mode: "Recipient will receive about X USDC on Solana. Estimated time N minutes. Fee F."
5. Sender signs a normal transfer to the returned address.

**C. Optional: receive as preferred asset**

1. Deposit lands at the Intents deposit address.
2. Market makers execute the swap and deliver the destination asset to the owner's Solana address.
3. The router polls status until a terminal state (success, failed, or refunded) and records it for the owner's dashboard.

**D. Optional: receive in a bank**

1. As in C, but the destination is the off-ramp provider's USDC deposit address (or a virtual account linked to the owner's wallet) on Solana.
2. The provider converts USDC to fiat and pays out to the owner's linked bank account over local rails.
3. The owner's dashboard shows each payment end to end: sent, swapped, off-ramped, settled.

**E. Failure paths (must be designed, not afterthoughts)**

- No wallet connected for the chain: the resolver returns `NO_RECORD`, the client shows an error, and nothing is sent.

* Below the route minimum: resolver returns the minimum and warns before signing.
* Swap cannot execute: funds are refunded to the sender's refund address; the dashboard shows the refund.
* Off-ramp rejects (verification lapsed, limit exceeded): funds stay as USDC in the owner's settlement wallet, flagged for action.

## Architecture

| Component | Language | Responsibility |
| --- | --- | --- |
| Name registry contract | Rust (near-sdk-rs) on NEAR | Ownership, one verified address record per chain stored as an ERC-7930 Interoperable Address, ENSIP-31 Payment Preferences, change delays, events |
| Resolver service | Rust (e.g. Axum) | `resolve` API (returns `NO_RECORD` when no wallet is connected), response signing, caching, rate limits |
| Web app | TypeScript | Wallet connection per chain with signed proof of control, claim and configure names |
| SDK | TypeScript (Rust crate later) | `resolve()` for wallets and apps, ERC-7930/ENSIP-31 encoding, response verification, clear error states |
| Router adapter (optional) | Rust | Aurora Intents persistent addresses (fallback: NEAR Intents 1Click quotes), status tracking, refund handling |
| Chain Signatures wallet manager (fallback) | Rust and the registry contract | Derives per-name wallets on other chains through the MPC signer, watches deposits, requests signatures for sweeps |
| Off-ramp adapter (optional) | Rust | Provider API (sandbox for demo), verification status, payout tracking, webhooks |

**Registry record (logical schema)**

```json
{
  "name": "peni.uni",
  "owner": "<controller account / key set>",
  "records": {
    "<chainType|chainRef, as a CAIP-2 chain ID or ERC-7930 Chain Identifier>": {
      "address": "<bytes, ERC-7930 Interoperable Address in native binary encoding>",
      "proof": "<signature from that wallet>"
    }
  },
  "receive_policy": {
    "default": {
      "asset": "USDC",
      "destination": {
        "type": "interop-address",
        "address": "<bytes, ERC-7930 Interoperable Address>",
        "chainId": "<chainType|chainRef>"
      }
    },
    "payout": { "type": "bank", "provider": "<id>", "ref": "<opaque ref>" },
    "force_default": false
  },
  "pending_change": { "effective_at": 0, "change": {} },
  "version": 1
}
```

Address storage choice: store addresses as ERC-7930 Interoperable Addresses (`version | chainType | chainRefLen | chainRef | addrLen | addr`) rather than as a `uint256 coinType` key. This matches how the **Chain Registry-Resolver** on `on.eth` and ENSv2's `mapping(uint256 coinType => bytes addressBytes)` evolve: the ENSIP-9/ENSIP-11 `coinType` keyspace (SLIP-44 / `0x80000000 | chainId`) is retained as an index for backward compatibility with existing multichain tooling, but the value is the canonical Interoperable Address. That keeps records compatible with legacy ENS clients (`addr(bytes32, coinType)`) while future-proofing against chains that don't fit a 32-bit key (e.g. EVM chains with `chainId > 10^32`) and against non-EVM namespaces.

**Resolution API (sketch)**

```
GET /v1/resolve?name=peni.uni&chain=eip155:1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48

200 {
  "name": "peni.uni",
  "mode": "direct",
  "resolved": {
    "interoperableAddress": "<bytes hex: ERC-7930 Interoperable Address>",
    "chainId": "eip155:1",
    "token": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
  }
}
```

Routed mode (owner's ENSIP-31 policy prefers USDC on Solana for ETH):

```
200 {
  "name": "peni.uni",
  "mode": "routed",
  "policy": {
    "sourceAsset": "ETH@eip155:1",
    "destinationAsset": "USDC@solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
    "enforce": "ENSIP-31 preferred-chain-for-asset"
  },
  "resolved": {
    "depositAddress": "0x...",
    "interoperableAddress": "<persistent deposit address bytes>",
    "chainId": "eip155:1",
    "outChainId": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
    "outToken": "USDC",
    "minAmount": "...",
    "estReceive": "...",
    "expiresAt": null,
    "warnings": [],
    "signature": "<resolver signature over the body>"
  }
}
```

404 / 409: `NAME_NOT_FOUND`, `NO_RECORD` ("peni.uni has no wallet connected for this chain"), `NO_ROUTE` ("no supported route for this token → supported list: ...").

## Optional receive modes: payments routing

These modes are optional. The core product (Mode 1) needs no router and no swap. The routed modes express the owner's **ENSIP-31 Payment Preferences**: for the asset the sender is using, which destination asset/chain the owner prefers. The resolver resolves that preference at send time against currently supported routes.

The NEAR Intents 1Click flow, as documented by NEAR's integration material: fetch supported tokens, request a quote (a dry quote returns pricing without committing; a live quote returns a deposit address), send funds to that address, optionally notify the API of the deposit, then poll status until it reaches success, failed or refunded. Supported chains include EVM chains, Solana, NEAR, TON, Stellar and Tron.

**Reusable addresses: solved in principle, with conditions.** A resolver is asked for an address *before* the sender decides an amount, and the sender's wallet will not call a quote endpoint. NEAR's 1Click quotes return single-use deposit addresses, and that breaks this flow. What was found:

1. Aurora Intents Persistent Addresses fits. Aurora's docs describe a permanent, reusable address with no expiry, tied to a source chain, a recipient and an optional output asset. The same inputs always return the same address. If the deposited asset differs from the output asset, a swap runs through NEAR Intents solvers. Aurora's COCA case study reports one persistent address per user per network across nine USDC networks. The API reference is live, but creating new persistent addresses is limited to organisations Aurora has approved; any other API key gets a 403. The name can serve as the \`sender\` identifier, and all EVM chains share one address. Keys come from Intents Studio. The collected fee is split 60% integrator and 40% Aurora, with an Aurora floor of 2 bps on stablecoins and 10 bps on other tokens, and a maximum fee of 500 bps.
2. 1Click alone does not fit. A live quote returns a unique single-use address, and NEAR Intents' separate Passive Deposit service gives a fixed address per account but credits the deposit to a NEAR Intents balance instead of delivering it to another chain.

**Fallback if Aurora approval has not arrived by Nov 11: wallets created with NEAR Chain Signatures.** The registry contract derives a wallet for each name on the other chains from its own NEAR account and a fixed path per name. The same inputs always give the same address, and no other NEAR account can control it. That derived address is the deposit address `resolve` returns, encoded as an ERC-7930 Interoperable Address per the CASA `chainType` registry (`eip155` for ECDSA/EVM chains, `bip122` for Bitcoin, `solana` for Solana, address in native encoding). When a deposit arrives, the contract requests a signature from the MPC signer and sweeps the funds; for a different asset or a bank payout, the sweep goes to a NEAR Intents 1Click deposit address for the swap. The same mechanism can also give an owner a wallet on a chain where they have none, which fills the `NO_RECORD` gap.

What to know about this path:

- Chain support: Chain Signatures signs for both ECDSA (secp256k1) and EdDSA (Ed25519) chains, which covers EVM chains, Bitcoin and Solana. The signer contract is `v1.signer` on mainnet and `v1.signer-prod.testnet` on testnet.
- Gas: a derived address needs native gas on its own chain to send anything, so a token deposit to an empty address cannot be swept without a gas top-up. NEAR documents a Multichain Gas Station contract for this; confirm it fits before relying on it.
- Signature cost: each request attaches a NEAR deposit that scales with pending requests, from 1 yoctoNEAR up to 0.65 NEAR. Unused deposit is refunded.
- Deposit detection: the service must watch each chain for incoming deposits. The Aurora route would do this for us.
- Trust: the signing path is operated by the service, so it needs per-name limits, an audit log and strict rules in the contract. This is closer to custody than the Aurora route, so compliance review matters more.
- Storage format: derive the stored address as an ERC-7930 Interoperable Address so the resolver can return a canonical chain-bound value for any chain type; confirm the CASA chainType slot for the chains you target (eip155, bip122, solana, etc.).

## Off-ramp to bank

Turning stablecoins into bank deposits requires licensing, banking relationships and compliance in every country served, so the practical path is integrating a licensed provider rather than building this. Several providers advertise USDC-on-Solana to bank payout APIs (for example SpherePay's wallet-linked virtual accounts, Crossmint, BlindPay, Bridge and Bastion). Providers screen receivers against sanctions lists and verify identity before releasing fiat, so the *recipient* must complete verification; the sender never does.

Provider choice depends on the recipient's country, currency and limits. This must be decided with current provider terms, not from this document.

## Security and abuse model

| Threat | Mitigation |
| --- | --- |
| Attacker changes a record or payout destination | Owner-signed changes only; time delay on payout and default changes; notifications; optional multi-key approval |
| Resolver or API compromise returns a wrong address | Signed responses; SDK verifies signature; registry state is the source of truth and can be read directly; show recipient summary before signing |
| Sender sends an unsupported token to a deposit address | Resolver answers only for supported pairs; return `NO_ROUTE` with the supported list; warnings in SDK |
| Lookalike or homograph names | Name normalization and confusable-character rules (ENS adopted a normalization standard for this), reserved-name list, display of the full normalized name |
| Quote manipulation or stale pricing | Show estimate with slippage bounds; refund path; monitor status to terminal state |
| Replay or forged ownership proof | Signed-message auth with nonce, domain binding and expiry (NEP-413 style) |
| Key custody for settlement wallets | Prefer provider-held virtual accounts or owner-controlled wallets; the service holds no user funds long term |
| Griefing or enumeration | Rate limits, caching, no bank data on-chain |

## Compliance notes

Bank payout means handling money movement that is regulated differently per country. The design keeps the service out of custody by routing through licensed providers, storing no bank details on-chain, and requiring recipient verification. This is a design intention, not legal advice; a real launch needs legal review per target market (licensing, travel-rule obligations, sanctions screening, consumer terms).

## Prior art and differentiation

**How the three relevant ecosystems handle names (feasibility check, Nov 9):**

- **Ethereum (ENS):** registry, registrar and resolver contracts live on Ethereum mainnet only. Multichain is data on L1: `addr(node, coinType)` records per ENSIP-9/11, no contracts on other chains needed for lookup. L2-based names (`jesse.base.eth`) keep the parent name on L1 and the data on the L2, resolved through CCIP-Read (EIP-3668): the resolver reverts with `OffchainLookup`, the client fetches from a gateway, and the L1 resolver verifies the gateway's signature. ENSv2 replaces the flat registry with hierarchical per-name registries and a Universal Resolver V2 entrypoint. Lesson: a namespace can live on one chain and address every other chain as stored bytes.
- **Solana (SNS):** domains are PDA accounts in the `spl-name-service` program, keyed by a hash of the name and TLD. Records V2 enforces two properties that match this design: *staleness* (records set by a previous owner become invalid on transfer) and *right of association* (a cross-chain record such as an ETH address is only trusted when the record owner signed proof of control). Cross-chain resolution exists but is heavyweight: mirror resolver contracts deployed on BNB, Base and Injective through Wormhole. `.sol.site` serves web2 DNS from on-chain records through a gateway. Lesson: owner-signed cross-chain records are a proven pattern; deploying mirror contracts per chain is the expensive alternative this design avoids.
- **NEAR:** names are protocol-level. The `registrar` account creates top-level accounts, `near` creates `*.near`, and any named account can create its own sub-accounts, so subdomains need no extra contract. Chain Signatures (`v1.signer` MPC) derives ECDSA addresses (EVM chains, Bitcoin) and EdDSA addresses (Solana and others) from one NEAR account and a derivation path, enabling outbound transactions; deposits must be watched separately and sweeps need gas on the target chain (Multichain Gas Station). Lesson: the registry design can rely on protocol-level names and MPC-derived wallets instead of deploying anything off NEAR.

**Feasibility verdict:** the design is consistent with all three and avoids the expensive parts. Like ENS, one chain (NEAR) holds the namespace and treats other chains as stored records; like SNS Records V2, every record carries an owner signature proving control; unlike SNS, no mirror contracts on other chains are needed because resolution is a read of NEAR state through the resolver service; and NEAR's Chain Signatures natively fill the "no wallet on this chain" gap that ENS and SNS cannot fill at all.

| System | What it does | Gap relative to this idea |
| --- | --- | --- |
| ENS multichain records | One name maps to addresses per chain, using SLIP-44 and EVM chain-ID coin types | Lookup only: pays out as sent, no conversion, no bank |
| Unstoppable Domains | Domains with records for many currencies; resolver libraries; a MetaMask Snap added cross-chain resolution across 23 chains in 2024 | Lookup only: same gap |
| [ENSIO](https://ethglobal.com/showcase/ensio-094o5), [ENSRouter](https://ethglobal.com/showcase/ensrouter-c4ksu), [ENS PayLinks](https://ethglobal.com/showcase/ens-paylinks-615g8) (HackMoney 2026 entries) | Store receive preferences (token, chain) in ENS records; payer pays any token from any chain; LI.FI routing delivers the recipient's preferred token | EVM and ENS centred; delivered through a payment link or app, not a plain wallet send; no bank payout; not NEAR Intents |
| [Ghost Wallet](https://github.com/tomdan-ai/ghost) (open source) | Username registry, payment links, LI.FI routing, settlement into Solana stablecoins | Closest to Mode 2 with Solana; link based; its README shows no bank payout |
| [Request Network](https://docs.request.network/api-features/crosschain-payments) | Recipient sets one settlement chain and stablecoin; payer's USDC or USDT from other chains is routed there | Request and payment-link flow, stablecoins on a limited set of chains, no name system, no bank |
| [Daimo Pay](https://paydocs.daimo.com/payment-links) | Payment links: payer pays in any token, receiver gets a chosen token on a chosen chain | Same: link or checkout flow, no name registry, no bank |
| [BVNK](https://www.bvnk.com/blog/blockchain-cross-border-payments) | Business stablecoin checkout, auto-converted to fiat | Licensed business product, not a name system |
| [cashingout.eth](https://www.producthunt.com/p/cashingout-eth) (older, Wyre based) | ENS address linked to a bank account; incoming crypto liquidated to the bank | Historic single-provider product; current status not verified |
| Aurora Intents with Solflare | Deposit addresses that route from other chains into a Solana wallet via NEAR Intents | Wallet funding feature, not a name system, no per-recipient policy, no bank |
| Stablecoin off-ramp APIs | USDC to bank payout | Not a naming or sender-side layer |

- **Honest read:** the pieces exist, and receive preferences are already built several times — and now standardized. Name-based receive preferences with cross-chain routing appear in the ENS-based entries above, Ghost, Request Network and Daimo Pay, mostly using LI.FI or similar routers and mostly delivered as a payment link. Bank settlement exists separately in licensed products. Two new standards now cover exactly the "preferred chain per asset" problem: ENSIP-31 Payment Preferences (a name publishes accepted assets and preferred chains per asset, with EIP-7930 chain IDs) and ERC-7930 / CAIP-350 (a canonical binary format that binds an address to its chain). That means receive preferences are a feature, not the pitch — and the smart move is to implement those standards in the registry so any client that supports ENSIP-31 can discover the owner's settlement chain without a custom integration. The searches did not find one product that combines a name registry honoring those standards, receiver-chosen routing through NEAR Intents, and bank payout. That is not proof none exists. Judges, including the AI judges, can search too and will find the ENS-based entries, so the pitch must not claim novelty for receive preferences. The core idea, one name with a wallet per chain, is what ENS and Unstoppable Domains already do, so the pitch should lead with what is NEAR-native: wallets on every chain created and managed from one NEAR account through Chain Signatures.

**Where this project can still be distinct:**

- NEAR-native: the registry is a Rust contract on NEAR, and the same NEAR account can create and manage wallets on other chains through Chain Signatures.
- Verified records: every chain record carries a signed proof of control from that wallet, so a typo or a hostile record cannot be saved.
- Honest failures: no wallet connected means a clear client error, never a guessed address.
- Standards-compliant receive policy: the registry encodes the owner's preferences as an **ENSIP-31 Payment Preferences** record and returns **ERC-7930 Interoperable Addresses**, so the resolution call works with any client implementing those standards.
- Optional receive modes (Aurora Intents or the Chain Signatures fallback, then bank payout) layered on top, not required for the core flow.
- If Aurora's persistent addresses are approved, a plain wallet send into routed modes, which link-based competitors do not offer.

## Hackathon scope

**Track fit:** Real-World Money (payments and remittances) is the most likely fit; Open Innovation is the alternative if judges read this as identity infrastructure. Chain Signatures and chain abstraction are the NEAR building blocks that carry the core.

**Deadline:** build window closes Nov 14, 2026 23:59 UTC. Internal target for a complete submission: Nov 14 12:00 UTC, so 7 days remain from today.

**Vertical slice (build this, nothing more):**

1. Claim a name and connect wallets for at least three chains (for example NEAR, Ethereum and Solana), each with a signed proof of control (registry contract on NEAR testnet).
2. Resolver API and SDK: `resolve(name, chain, token)` returns the connected address. A chain with no wallet returns `NO_RECORD` and the demo app shows the error.
3. Demo send: pay the name on two chains with a testnet transfer and see each payment arrive at the owner's own wallet.
4. Optional receive mode, if time allows: preferred asset through Aurora Persistent Addresses if approved, otherwise the Chain Signatures fallback.
5. Optional: bank payout against a provider sandbox, or a clearly labelled simulation.

**Explicitly out of scope:** custom TLD registration with ICANN, native wallet resolution of the new suffix, mobile app, multi-provider off-ramp, governance or token.

**Demo script ("do X, see Y" for judges):**

1. Open the app, connect a NEAR wallet, and claim `demo.uni`. See the name appear in the dashboard.
2. Connect an Ethereum wallet and a Solana wallet and sign each proof. See both records saved on testnet with an explorer link.
3. Resolve the name for Ethereum. See the Ethereum address. Resolve it for Bitcoin. See the "no wallet connected" error in the client.
4. Send a small testnet payment to `demo.uni` on Ethereum. See it arrive at the connected wallet.
5. Optional: switch the receive policy to USDC on Solana and resolve for Ethereum. See a deposit address and an estimated amount.

**Day plan:** Nov 9-10 registry contract and resolver for the core flow, and request Aurora approval in parallel; Nov 11 web app with wallet connection per chain, plus the SDK; Nov 12 optional receive mode (Aurora if approved, otherwise Chain Signatures) and security review; Nov 13 tests, README, demo video; Nov 14 morning submission pack.

## Open questions and unverified assumptions

1. **Name suffix.** `.uni` is unconfirmed. Wallets will not resolve a new suffix natively, so adoption needs SDK or wallet integration (a precedent is the MetaMask Snap approach) or a gateway through an existing system such as an ENS wildcard resolver. Also check for naming conflicts before committing.
2. **Reusable deposit addresses** Answered in principle by Aurora Intents Persistent Addresses, which is live but gated: new addresses need Aurora approval. Still to confirm: approval for this project, minimum deposit amounts, and delivery to an off-ramp provider's address.
3. **Aurora Intents API access** for third parties: approval is required to create persistent addresses. Fees are published (see the optional receive modes section).
4. **Testnet availability** for Intents. If none, the demo needs small mainnet amounts or a recorded run, and the README must say so.
5. **Fees.** Third-party notes mention a small fee unless authenticated with a partner key, and a small per-swap minimum. Confirm current numbers in official docs before quoting any to users.
6. **Off-ramp provider and corridor** for the target currencies (including Naira if that is a goal) and the verification requirements for recipients.
7. **Whether `force_default` should be on by default.** It simplifies the story but removes the owner's ability to receive native assets directly.

Added for the core framing:

- Chain Signatures fallback: confirm how derived addresses get gas for sweeps (NEAR documents a Multichain Gas Station contract) and whether the registry contract can safely hold the signing path.
- Proof of control: pick the signing method per chain for the connect-wallet challenge (message signing on EVM and Solana, NEP-413 on NEAR).

## Sources

- ENSIP-9, Multichain Address Resolution: https://docs.ens.domains/ensip/9
- ENSIP-11, EVM Chain Address Resolution: https://docs.ens.domains/ensip/11
- ENSIP-19, Multichain Primary Names: https://docs.ens.domains/ensip/19
- ENSIP-23, Universal Resolver: https://docs.ens.domains/ensip/23
- ENSIP-31, Payment Preferences (draft): https://docs.ens.domains/ensip/31
- ERC-7930, Interoperable Addresses (review): https://eips.ethereum.org/EIPS/eip-7930
- ERC-7996, Contract Feature Detection: https://eips.ethereum.org/EIPS/eip-7996
- CAIP-2: https://github.com/ChainAgnostic/CAIPs/blob/master/CAIPs/caip-2.md
- CAIP-10: https://github.com/ChainAgnostic/CAIPs/blob/master/CAIPs/caip-10.md
- CAIP-104, Namespace Reference: https://github.com/ChainAgnostic/CAIPs/blob/master/CAIPs/caip-104.md
- CAIP-350, Binary Serialization of Blockchain IDs and Addresses: https://github.com/ChainAgnostic/CAIPs/blob/master/CAIPs/caip-350.md
- ENS wildcard resolution and name normalization (web3.py docs): https://web3py.readthedocs.io/en/latest/\_sources/ens\_overview.rst.txt
- Unstoppable Domains, crypto payments and resolution: https://docs.unstoppabledomains.com/crypto-payments
- Unstoppable Domains MetaMask Snap coverage: https://ffnews.com/blog/2024/10/17/4-million-unstoppable-domain-users-gain-cross-chain-name-resolution-on-metamask
- NEAR Intents 1Click API integration docs: https://docs.near-intents.org/near-intents/integration/distribution-channels/1click-api
- NEAR agent skill for Intents (flow, endpoints, status states): https://playbooks.com/skills/near/agent-skills/near-intents
- NEAR Intents FAQ (deposits, addresses): https://docs.near-intents.org/resources/faqs
- Aurora Intents with Solflare: https://cryptobriefing.com/solflare-launches-simpler-cross-chain-deposits-powered-by-aurora-intents/
- Third-party 1Click notes on fees and minimums: https://tomevault.io/tome/cuongdcdev/openclaw-near-skills
- Stablecoin off-ramp overview and licensing: https://crossmint.com/learn/stablecoin-offramp-guide and https://blindpay.com/resources/more/what-is-a-stablecoin-offramp
- SpherePay off-ramp wallets: https://docs.spherepay.co/guide/transfer/automating-transfer/off-ramp
- Bastion off-ramp: https://docs.bastion.com/guides/quickstarts/off-ramp.md
- SNS Records V2 (staleness, right of association): https://docs.sns.id/collection/sns-v1/records.md
- SNS cross-chain bridged domains (Wormhole mirror contracts): https://docs.sns.id/collection/sns-v1/cross-chain-domain-experimental.md and https://docs.sns.id/collection/sns-v2/using-sns.md
- Bonfida SNS key derivations (PDA scheme): https://github.com/Bonfida/solana-name-service-guide
- Sol.site DNS-over-SNS gateway: https://docs.sns.id/collection/sns-v2/sol.site/how-it-works.md
- NEAR Accounts and named addresses (registrar): https://docs.near.org/protocol/accounts-contracts/account-model.md and https://docs.near.org/protocol/accounts-contracts/account-id.md
- CAIP-2 profiles per namespace (eip155, solana, bip122, starknet): https://github.com/ChainAgnostic/namespaces

Added after the prior-art check: ENSIO, ENSRouter and ENS PayLinks (ETHGlobal showcase pages), the Ghost Wallet repository, Request Network, Daimo Pay, BVNK and cashingout.eth, all linked in the Prior Art table.

Added after the reusable-address check: [Aurora Persistent Addresses](https://docs.intents.aurora.dev/intents-deposits/persistent-addresses), [What are Intents Deposits](https://docs.intents.aurora.dev/intents-deposits/what-are-intents-deposits), [Aurora API integration](https://docs.intents.aurora.dev/intents-deposits/quickstart/api-integration), [Aurora UX recommendations](https://docs.intents.aurora.dev/intents-connect/integration-best-practices/ux-recommendations), [COCA case study](https://x.com/auroraisnear/article/2092949085758058979), [1Click single-use addresses](https://mpp.dev/payment-methods/nearintents/charge), [NEAR Intents Passive Deposit service](https://docs.near-intents.org/near-intents/market-makers/passive-deposit-withdrawal-service).

Added after the API check: [Create persistent deposit address (API reference)](https://docs.intents.aurora.dev/api-reference/persistent-addresses-api-reference/create-persistent-deposit-address.md), [Persistent deposit status](https://docs.intents.aurora.dev/api-reference/persistent-addresses-api-reference/get-persistent-deposit-status.md), [API Keys and Fees](https://docs.intents.aurora.dev/getting-started/api-keys-and-fees.md).

Added for the Chain Signatures fallback: [Chain Signatures overview](https://docs.near.org/chain-abstraction/chain-signatures), [Implementing Chain Signatures](https://docs.near.org/chain-abstraction/chain-signatures/implementation), [Getting started](https://docs.near.org/chain-abstraction/chain-signatures/getting-started).
