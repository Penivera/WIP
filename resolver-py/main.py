import time
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from cs_manager import ChainSignaturesManager
from erc7930 import (
    SOLANA_MAINNET_CAIP2,
    canonicalize_chain_key,
    encode_erc7930,
    format_native_address,
)
from near_client import NearContractClient
from offramp_adapter import OffRampAdapter
from proofs import (
    RESOLVER_PUBKEY_HEX,
    sign_resolver_response,
    validate_and_normalize_name,
    verify_chain_proof,
)
from router_adapter import RouterAdapter
from checkout_adapter import CheckoutAdapter

app = FastAPI(title="Universal Name (.uni) Resolver & Router Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

near_client = NearContractClient()
cs_manager = ChainSignaturesManager()
router = RouterAdapter(cs_manager)
offramp = OffRampAdapter()
checkout_adapter = CheckoutAdapter(near_client, cs_manager)

# Tracks routed payment statuses for the Owner Dashboard (Spec Flows C, D, E)
PAYMENT_DASHBOARD: dict[str, dict] = {}


def _extract_policy_target(policy: dict) -> tuple[str, str, str, str, dict | None]:
    """
    Extracts (mode, dest_chain, dest_asset_str, recipient_address, payout_meta)
    from the Rust contract's ReceivePolicy JSON.
    """
    default_opt = policy.get("default")
    payout_opt = policy.get("payout")

    fallback_chain = SOLANA_MAINNET_CAIP2
    fallback_asset = "USDC"
    fallback_addr = "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"

    if default_opt and "Asset" in default_opt:
        asset_obj = default_opt["Asset"]
        fallback_chain = asset_obj["chain"]
        fallback_asset = asset_obj["asset"]
        fallback_addr = format_native_address(fallback_chain, bytes(asset_obj["address"]))

    if payout_opt and "Payout" in payout_opt:
        payout_obj = payout_opt["Payout"]
        ref = payout_obj["reference"]
        prov_entry = offramp.get_provider_ref(ref)
        if prov_entry and prov_entry.get("kycStatus") == "VERIFIED":
            recipient = prov_entry["virtualSolanaUsdcAddress"]
        else:
            recipient = fallback_addr
        return (
            "bank",
            SOLANA_MAINNET_CAIP2,
            "USDC",
            recipient,
            {
                "type": "bank",
                "provider": payout_obj["provider"],
                "ref": ref,
                "fallbackAddress": fallback_addr,
            },
        )

    return ("routed", fallback_chain, fallback_asset, fallback_addr, None)


@app.get("/v1/tokens")
async def get_supported_tokens():
    """Returns the cached 1Click supported tokens catalog."""
    return await router.get_supported_tokens()


@app.get("/v1/resolve")
async def resolve(
    name: str = Query(..., description="Universal Name, e.g. peni.uni"),
    chain: str = Query(..., description="CAIP-2 chain ID or short name, e.g. eip155:1, base"),
    token: str = Query("native", description="Token symbol, contract address, or 'native'"),
    amount: str | None = Query(None, description="Optional input amount in smallest base units"),
    refund_to: str | None = Query(None, description="Optional sender refund address on origin chain"),
    dry: bool = Query(False, description="Set true for dry quote preview without deposit address"),
    live_quote: bool = Query(True, description="Set true to query live 1Click API quote"),
):
    try:
        clean_name = validate_and_normalize_name(name)
        ns, ref, coin_type = canonicalize_chain_key(chain)
        norm_chain = f"{ns}:{ref}"
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": str(err)})


    # STEP 1: Unknown Name -> 404 NAME_NOT_FOUND

    record = await near_client.get_record(clean_name)
    if not record:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NAME_NOT_FOUND",
                "message": f"{clean_name} is not registered.",
            },
        )

    owner = record.get("owner", "")
    record_gen = int(record.get("generation", 1))
    chains_map = record.get("chains") or {}
    policy = record.get("policy")
    has_policy_target = bool(policy and (policy.get("default") or policy.get("payout")))
    force_default = bool(has_policy_target and policy.get("force_default", False))

    async def build_routed_response() -> dict:
        mode, dest_chain, dest_asset, recipient_addr, payout_meta = _extract_policy_target(policy)
        try:
            route_res = await router.resolve_route(
                name=clean_name,
                source_chain=norm_chain,
                source_token=token,
                dest_chain=dest_chain,
                dest_asset_str=dest_asset,
                recipient_address=recipient_addr,
                amount_in=amount,
                refund_to=refund_to,
                dry=dry,
                live_1click_quote=live_quote,
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail={"error": str(err)})

        if "error" in route_res:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "NO_ROUTE",
                    "message": f"No supported route for token '{token}' on {norm_chain}.",
                    "supported": route_res.get("supported", []),
                },
            )

        dep_addr = route_res["depositAddress"]
        out_sym = route_res["destSymbol"]
        src_sym = route_res["sourceSymbol"]
        est_mins = max(1, round(route_res["timeEstimateSeconds"] / 60))

        PAYMENT_DASHBOARD[dep_addr] = {
            "name": clean_name,
            "depositAddress": dep_addr,
            "depositMemo": route_res["depositMemo"],
            "mode": mode,
            "sourceAsset": f"{src_sym}@{norm_chain}",
            "destinationAsset": f"{out_sym}@{dest_chain}",
            "estReceive": route_res["estReceiveFormatted"],
            "status": "PENDING_DEPOSIT",
            "payoutMeta": payout_meta,
            "createdAt": int(time.time()),
            "updatedAt": int(time.time()),
        }

        body = {
            "name": clean_name,
            "mode": mode,
            "policy": {
                "sourceAsset": f"{src_sym}@{norm_chain}",
                "destinationAsset": f"{out_sym}@{dest_chain}",
                "enforce": "ENSIP-31 preferred-chain-for-asset",
                "payout": (
                    {"type": "bank", "provider": payout_meta["provider"], "ref": payout_meta["ref"]}
                    if payout_meta
                    else None
                ),
            },
            "resolved": {
                "depositAddress": dep_addr,
                "depositMemo": route_res["depositMemo"],
                "chainSignaturesFallbackAddress": route_res["chainSignaturesFallbackAddress"],
                "interoperableAddress": route_res["interoperableAddress"],
                "chainId": norm_chain,
                "outChainId": dest_chain,
                "outToken": out_sym,
                "swapType": route_res["swapType"],
                "provider": route_res["provider"],
                "minAmount": route_res["minAmount"],
                "estReceive": route_res["estReceive"],
                "estReceiveFormatted": route_res["estReceiveFormatted"],
                "timeEstimateSeconds": route_res["timeEstimateSeconds"],
                "fee": route_res["fee"],
                "expiresAt": route_res["expiresAt"],
                "timeWhenInactive": route_res["timeWhenInactive"],
                "oneclickSignature": route_res["oneclickSignature"],
                "correlationId": route_res["correlationId"],
                "summary": (
                    f"Recipient will receive about {route_res['estReceiveFormatted']} {out_sym} on {dest_chain.split(':')[0].capitalize()}"
                    + (" (settled to linked bank account)." if mode == "bank" else ".")
                    + f" Estimated time {est_mins} min. Fee {route_res['fee']['totalFeeBps']} bps."
                ),
                "warnings": route_res["warnings"],
                "resolverPubkey": RESOLVER_PUBKEY_HEX,
            },
        }
        sig = sign_resolver_response(body)
        body["resolved"]["signature"] = sig
        body["signature"] = sig
        return body


    # STEP 2: If force_default is True -> Route via Receive Policy

    if force_default:
        return await build_routed_response()


    # STEP 3: Direct Connected Wallet Lookup (if present & proof is valid)

    chain_rec = chains_map.get(norm_chain)
    if chain_rec:
        raw_addr = bytes(chain_rec["address"])
        raw_proof = bytes(chain_rec["proof"])
        chain_gen = int(chain_rec.get("generation", record_gen))

        proof_ok, proof_reason = verify_chain_proof(
            name=clean_name,
            owner=owner,
            chain_key=norm_chain,
            raw_address=raw_addr,
            raw_proof=raw_proof,
            record_generation=record_gen,
            chain_generation=chain_gen,
        )
        if not proof_ok:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "UNVERIFIED_OR_STALE_RECORD",
                    "reason": proof_reason,
                    "message": f"Record for {clean_name} on {norm_chain} has an invalid or stale proof.",
                },
            )

        human_addr = format_native_address(norm_chain, raw_addr)
        interop_hex = encode_erc7930(norm_chain, raw_addr)

        body = {
            "name": clean_name,
            "mode": "direct",
            "resolved": {
                "address": human_addr,
                "interoperableAddress": interop_hex,
                "chainId": norm_chain,
                "coinType": chain_rec.get("coin_type", coin_type),
                "token": token,
                "proofStatus": proof_reason,
                "generation": chain_gen,
                "resolverPubkey": RESOLVER_PUBKEY_HEX,
            },
        }
        sig = sign_resolver_response(body)
        body["resolved"]["signature"] = sig
        body["signature"] = sig
        return body


    # STEP 4: Fallback to Default Receive Policy if Configured

    if has_policy_target:
        return await build_routed_response()


    # STEP 5: Honest Failure -> NO_RECORD

    raise HTTPException(
        status_code=404,
        detail={
            "error": "NO_RECORD",
            "name": clean_name,
            "chain": norm_chain,
            "message": f"{clean_name} has no wallet connected for {norm_chain}",
        },
    )


#  1CLICK DEPOSIT SUBMIT & LIVE STATUS TRACKING ENDPOINTS

class SubmitDepositRequest(BaseModel):
    depositAddress: str
    txHash: str
    nearSenderAccount: str | None = None
    memo: str | None = None


@app.post("/v1/deposit/submit")
async def submit_deposit(req: SubmitDepositRequest):
    """Notifies 1Click of an on-chain deposit transaction hash (POST /v0/deposit/submit)."""
    res = await router.submit_deposit_tx(
        deposit_address=req.depositAddress,
        tx_hash=req.txHash,
        near_sender_account=req.nearSenderAccount,
        memo=req.memo,
    )
    if req.depositAddress in PAYMENT_DASHBOARD:
        PAYMENT_DASHBOARD[req.depositAddress]["txHash"] = req.txHash
        PAYMENT_DASHBOARD[req.depositAddress]["status"] = res.get("status", "KNOWN_DEPOSIT_TX")
        PAYMENT_DASHBOARD[req.depositAddress]["updatedAt"] = int(time.time())
    return res


@app.get("/v1/status")
async def check_swap_status(
    depositAddress: str = Query(..., description="Deposit address from quote response"),
    depositMemo: str | None = Query(None, description="Optional deposit memo if required"),
):
    """Polls 1Click GET /v0/status and updates the recipient dashboard state."""
    status_res = await router.get_execution_status(depositAddress, depositMemo)
    live_status = status_res.get("status", "PENDING_DEPOSIT")

    if depositAddress in PAYMENT_DASHBOARD:
        entry = PAYMENT_DASHBOARD[depositAddress]
        entry["status"] = live_status
        entry["swapDetails"] = status_res.get("swapDetails")
        entry["updatedAt"] = int(time.time())

        if live_status == "SUCCESS" and entry["mode"] == "bank" and entry.get("payoutMeta"):
            pm = entry["payoutMeta"]
            receipt = offramp.execute_payout(pm["ref"], entry["estReceive"], pm["fallbackAddress"])
            entry["status"] = receipt["status"]
            entry["offrampReceipt"] = receipt

    return status_res


# PHASE 3: CHAIN SIGNATURES DERIVATION ENDPOINT

@app.get("/v1/cs/derive")
async def cs_derive_address(name: str, chain: str):
    try:
        clean_name = validate_and_normalize_name(name)
        return cs_manager.derive_deposit_address(clean_name, chain)
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": str(err)})


# DASHBOARD & DEMO SIMULATION ENDPOINTS

@app.get("/v1/dashboard/{name}")
async def get_dashboard(name: str):
    try:
        clean_name = validate_and_normalize_name(name)
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": str(err)})
    items = [v for v in PAYMENT_DASHBOARD.values() if v["name"] == clean_name]
    return {"name": clean_name, "payments": items, "csAuditLog": cs_manager.audit_log}


class SimulateTransitionRequest(BaseModel):
    deposit_address: str
    event: str = "SUCCESS"  # "SUCCESS", "REFUNDED", "OFFRAMP_REJECT"


@app.post("/v1/demo/transition")
async def simulate_payment_transition(req: SimulateTransitionRequest):
    if req.deposit_address not in PAYMENT_DASHBOARD:
        raise HTTPException(status_code=404, detail={"error": "DEPOSIT_NOT_FOUND"})

    entry = PAYMENT_DASHBOARD[req.deposit_address]
    payout_meta = entry.get("payoutMeta")

    if req.event == "OFFRAMP_REJECT" and payout_meta:
        offramp.set_kyc_status(payout_meta["ref"], "LAPSED")
        receipt = offramp.execute_payout(
            payout_meta["ref"], entry["estReceive"], payout_meta["fallbackAddress"]
        )
        entry["status"] = receipt["status"]
        entry["offrampReceipt"] = receipt
    elif req.event == "SUCCESS":
        if entry["mode"] == "bank" and payout_meta:
            offramp.set_kyc_status(payout_meta["ref"], "VERIFIED")
            receipt = offramp.execute_payout(
                payout_meta["ref"], entry["estReceive"], payout_meta["fallbackAddress"]
            )
            entry["status"] = receipt["status"]
            entry["offrampReceipt"] = receipt
        else:
            entry["status"] = "SUCCESS"
    else:
        entry["status"] = req.event

    return entry


# MULTICHAIN STABLECOIN CHECKOUT ENDPOINTS (Domain Purchasing)

class CreateCheckoutRequest(BaseModel):
    name: str
    owner: str
    chain: str
    token: str = "USDC"
    refund_to: str | None = None


class SubmitCheckoutPaymentRequest(BaseModel):
    checkout_id: str
    tx_hash: str


class SimulateCheckoutRequest(BaseModel):
    checkout_id: str
    event: str = "SUCCESS"


@app.get("/v1/checkout/chains")
async def get_checkout_supported_chains():
    """Returns all supported blockchains and accepted stablecoins for domain purchase."""
    return checkout_adapter.get_supported_stables_catalog()


@app.get("/v1/checkout/pricing")
async def get_checkout_pricing(
    name: str = Query(..., description="Universal Name, e.g. alice.uni"),
):
    """Returns domain availability, length-tiered pricing, and payment channels."""
    try:
        return await checkout_adapter.get_pricing_and_availability(name)
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": str(err)})


@app.post("/v1/checkout/session")
async def create_checkout_session(req: CreateCheckoutRequest):
    """
    Creates a new domain purchase checkout session.
    Generates a deposit address on the user's chosen blockchain for stablecoin payment.
    """
    try:
        return await checkout_adapter.create_checkout_session(
            name=req.name,
            owner=req.owner,
            chain=req.chain,
            token=req.token,
            refund_to=req.refund_to,
        )
    except ValueError as err:
        msg = str(err)
        if "already registered" in msg:
            raise HTTPException(status_code=409, detail={"error": "NAME_TAKEN", "message": msg})
        raise HTTPException(status_code=400, detail={"error": "CHECKOUT_FAILED", "message": msg})


@app.get("/v1/checkout/session/{checkout_id}")
async def get_checkout_session(checkout_id: str):
    """Retrieves current checkout session details, payment status, and NEAR settlement info."""
    session = checkout_adapter.get_checkout_session(checkout_id)
    if not session:
        raise HTTPException(status_code=404, detail={"error": "CHECKOUT_NOT_FOUND"})
    return session


@app.post("/v1/checkout/submit")
async def submit_checkout_payment(req: SubmitCheckoutPaymentRequest):
    """
    Submits on-chain payment txHash. Settles domain claim on the NEAR contract via relayer.
    """
    try:
        return await checkout_adapter.submit_payment(
            checkout_id=req.checkout_id,
            tx_hash=req.tx_hash,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": str(err)})


@app.post("/v1/checkout/simulate")
async def simulate_checkout_payment(req: SimulateCheckoutRequest):
    """Simulates payment completion for test/demo environments."""
    try:
        return await checkout_adapter.simulate_payment(
            checkout_id=req.checkout_id,
            event=req.event,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": str(err)})


class ApprovePolicyRequest(BaseModel):
    name: str


@app.post("/v1/admin/approve-policy")
async def admin_approve_policy(req: ApprovePolicyRequest):
    """
    Admin path to approve and immediately apply a queued destination / policy change,
    bypassing the standard time delay.
    """
    try:
        clean_name = validate_and_normalize_name(req.name)
        return await near_client.admin_approve_pending_policy(clean_name)
    except ValueError as err:
        raise HTTPException(status_code=400, detail={"error": str(err)})


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
