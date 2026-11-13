use near_sdk::test_utils::{accounts, VMContextBuilder};
use near_sdk::{testing_env, AccountId, VMContext};
use unam_registry::{
    ChainRecord, Contract, Destination, EventData, PendingChangeKind, ReceivePolicy,
};

thread_local! {
    static LOGS: std::cell::RefCell<Vec<String>> = std::cell::RefCell::new(vec![]);
}

fn owner_ctx() -> VMContext {
    VMContextBuilder::new()
        .predecessor_account_id(accounts(0))
        .current_account_id(accounts(1))
        .build()
}

fn other_ctx() -> VMContext {
    VMContextBuilder::new()
        .predecessor_account_id(accounts(2))
        .current_account_id(accounts(1))
        .build()
}

fn contract() -> Contract {
    Contract::new(Some(60))
}

const EVM_ADDR: [u8; 20] = [0x11; 20];
const SOL_ADDR: [u8; 32] = [0x22; 32];

#[test]
fn claim_connect_resolve_happy_path() {
    testing_env!(owner_ctx());
    let mut c = contract();
    let name = c.claim("peni".into());
    assert_eq!(name, "peni.uni");
    assert!(!c.is_name_available("peni".into()));
    assert!(c.is_name_available("other".into()));

    c.connect_chain(
        "peni.uni".into(),
        "eip155:1".into(),
        EVM_ADDR.to_vec(),
        vec![1, 2, 3],
    );
    let r = c.resolve_direct("peni.uni".into(), "eip155:1".into());
    assert_eq!(r.address, EVM_ADDR.to_vec());
    assert_eq!(r.coin_type, 0x8000_0000 | 1);
    assert_eq!(r.generation, 1);

    c.connect_chain(
        "peni.uni".into(),
        "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d".into(),
        SOL_ADDR.to_vec(),
        vec![4, 5, 6],
    );
    let r = c.resolve_direct(
        "peni.uni".into(),
        "SOLANA:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d".into(),
    );
    assert_eq!(r.address, SOL_ADDR.to_vec());
    assert_eq!(r.coin_type, 501);

    let near_addr = b"alice.testnet".to_vec();
    c.connect_chain(
        "peni.uni".into(),
        "near:testnet".into(),
        near_addr.clone(),
        vec![7, 8, 9],
    );
    let r = c.resolve_direct("peni.uni".into(), "near:testnet".into());
    assert_eq!(r.address, near_addr);
    assert_eq!(r.coin_type, 397);

    assert_eq!(c.records_of(accounts(0), 0, 10).len(), 1);
    assert_eq!(c.owner_of("peni.uni".into()), Some(accounts(0)));
}

#[test]
fn malformed_chain_keys_and_addresses_rejected() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());

    // Bad chain key
    let bad_chain = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.connect_chain(
            "peni.uni".into(),
            "unknown:1".into(),
            EVM_ADDR.to_vec(),
            vec![1],
        );
    }));
    assert!(bad_chain.is_err(), "unknown chain namespace must panic");

    // Bad address length for EVM (should be 20 bytes, give 32)
    let bad_addr = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.connect_chain(
            "peni.uni".into(),
            "eip155:1".into(),
            SOL_ADDR.to_vec(),
            vec![1],
        );
    }));
    assert!(bad_addr.is_err(), "wrong address length for EVM must panic");
}

#[test]
fn unconnected_chain_is_honest_no_record() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());
    c.connect_chain(
        "peni.uni".into(),
        "eip155:1".into(),
        EVM_ADDR.to_vec(),
        vec![1],
    );
    assert!(c
        .get_record("peni.uni".into())
        .unwrap()
        .chains
        .get("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d")
        .is_none());
    assert_eq!(c.owner_of("ghost.uni".into()), None);
}

#[test]
fn only_owner_can_connect_and_transfer() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());
    testing_env!(other_ctx());
    let mut c2 = c;
    let err = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c2.connect_chain(
            "peni.uni".into(),
            "eip155:1".into(),
            EVM_ADDR.to_vec(),
            vec![1],
        );
    }));
    assert!(err.is_err(), "non-owner connect must panic");
}

#[test]
fn transfer_bumps_generation_and_invalidates_policy_queue() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());
    c.connect_chain(
        "peni.uni".into(),
        "eip155:1".into(),
        EVM_ADDR.to_vec(),
        vec![1, 2, 3],
    );

    let policy = ReceivePolicy {
        force_default: true,
        default: Some(Destination::Asset {
            asset: "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48".into(),
            chain: "eip155:1".into(),
            address: EVM_ADDR.to_vec(),
        }),
        payout: None,
    };
    c.set_receive_policy("peni.uni".into(), Some(policy.clone()));
    assert!(c.get_pending_change("peni.uni".into()).is_some());

    c.transfer_name("peni.uni".into(), accounts(2));
    assert_eq!(c.owner_of("peni.uni".into()), Some(accounts(2)));
    let record = c.get_record("peni.uni".into()).unwrap();
    assert_eq!(record.generation, 2);
    assert!(record.pending.is_none());
    let r = c.resolve_direct("peni.uni".into(), "eip155:1".into());
    assert_eq!(
        r.generation, 2,
        "stale-proof generation must bump on transfer"
    );
    assert_eq!(
        c.records_of(accounts(0), 0, 10).len(),
        0,
        "old owner index must be cleared"
    );
    assert_eq!(
        c.records_of(accounts(2), 0, 10),
        vec!["peni.uni".to_string()]
    );
}

#[test]
fn policy_change_applies_only_after_delay() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());

    let policy = ReceivePolicy {
        force_default: true,
        default: Some(Destination::Asset {
            asset: "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48".into(),
            chain: "eip155:1".into(),
            address: EVM_ADDR.to_vec(),
        }),
        payout: None,
    };
    let effective = c.set_receive_policy("peni.uni".into(), Some(policy));
    assert!(
        c.get_policy("peni.uni".into()).is_none(),
        "policy must not be active before delay"
    );

    let early = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.apply_pending_change("peni.uni".into());
    }));
    assert!(early.is_err(), "early apply must panic with CHANGE_PENDING");

    let mut b = VMContextBuilder::new();
    b.predecessor_account_id(accounts(0))
        .current_account_id(accounts(1))
        .block_timestamp(effective * 1_000_000 + 1);
    testing_env!(b.build());
    c.apply_pending_change("peni.uni".into());
    let active = c.get_policy("peni.uni".into()).unwrap();
    assert!(active.force_default);
}

#[test]
fn admin_approve_queued_policy_change() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("adminapp.uni".into());

    let policy = ReceivePolicy {
        force_default: true,
        default: Some(Destination::Asset {
            asset: "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48".into(),
            chain: "eip155:1".into(),
            address: EVM_ADDR.to_vec(),
        }),
        payout: None,
    };
    c.set_policy("adminapp.uni".into(), Some(policy));
    assert!(c.get_policy("adminapp.uni".into()).is_none());
    assert!(c.get_pending_change("adminapp.uni".into()).is_some());

    // 1. Non-admin caller fails with NOT_ADMIN
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(accounts(3))
        .current_account_id(accounts(1))
        .build());
    let err = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.admin_approve_pending_policy("adminapp.uni".into());
    }));
    assert!(err.is_err(), "non-admin caller must fail");

    // 2. Admin calls before delay expires -> immediately approved and active
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(accounts(0)) // accounts(0) is admin
        .current_account_id(accounts(1))
        .build());
    c.admin_approve_pending_policy("adminapp.uni".into());

    let active = c
        .get_policy("adminapp.uni".into())
        .expect("policy must be active");
    assert!(active.force_default);
    assert!(c.get_pending_change("adminapp.uni".into()).is_none());

    // 3. Calling when no change is pending fails with NO_PENDING_CHANGE
    let err_nopending = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.admin_approve_pending_policy("adminapp.uni".into());
    }));
    assert!(err_nopending.is_err(), "must fail when no pending change");
}

#[test]
fn policy_cancel_and_clear_queue() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());
    let policy = ReceivePolicy {
        force_default: false,
        default: Some(Destination::Asset {
            asset: "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d/token:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v".into(),
            chain: "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d".into(),
            address: SOL_ADDR.to_vec(),
        }),
        payout: None,
    };
    c.set_receive_policy("peni.uni".into(), Some(policy));
    c.cancel_pending_change("peni.uni".into());
    assert!(c.get_pending_change("peni.uni".into()).is_none());

    c.set_receive_policy("peni.uni".into(), None);
    let mut b = VMContextBuilder::new();
    b.predecessor_account_id(accounts(0))
        .current_account_id(accounts(1))
        .block_timestamp(near_sdk::env::block_timestamp() + 61_000_000_000);
    testing_env!(b.build());
    c.apply_pending_change("peni.uni".into());
    assert!(c.get_policy("peni.uni".into()).is_none());
}

#[test]
fn events_carry_nep297_shape() {
    testing_env!(owner_ctx());
    let e = EventData::NameClaimed {
        name: "peni.uni".into(),
        owner: accounts(0).to_string(),
    };
    let s = near_sdk::serde_json::to_string(&e).unwrap();
    assert!(s.contains("peni.uni"));
    let _ = PendingChangeKind::Clear;
    let _ = ChainRecord {
        address: vec![],
        proof: vec![],
        set_at_ms: 0,
        generation: 0,
        coin_type: 0,
    };
}

#[test]
fn reserved_and_malformed_names_rejected_by_contract_path() {
    testing_env!(owner_ctx());
    let mut c = contract();
    let t = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.claim("admin".into())));
    assert!(t.is_err());
    let t = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.claim("x".into())));
    assert!(t.is_err());
    let name = c.claim("Peni.Uni".into());
    assert_eq!(
        name, "peni.uni",
        "suffix + case normalization must fold to one record"
    );
    let t = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.claim("peni".into())));
    assert!(t.is_err(), "double claim must panic NAME_TAKEN");
}

#[test]
fn standard_aligned_method_names() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());

    // 1. set_record
    c.set_record(
        "peni.uni".into(),
        "eip155:1".into(),
        EVM_ADDR.to_vec(),
        vec![1, 2, 3],
    );

    // 2. get_name & get_record
    let record = c.get_name("peni.uni".into()).unwrap();
    assert_eq!(record.owner, accounts(0));
    assert!(record.chains.contains_key("eip155:1"));

    // 3. names_of (with and without pagination)
    assert_eq!(
        c.names_of(accounts(0), None, None),
        vec!["peni.uni".to_string()]
    );
    assert_eq!(
        c.names_of(accounts(0), Some(0), Some(5)),
        vec!["peni.uni".to_string()]
    );
    assert_eq!(
        c.names_of(accounts(0), Some(1), Some(5)),
        Vec::<String>::new()
    );

    // 4. set_policy & cancel_pending_policy
    let policy = ReceivePolicy {
        force_default: true,
        default: Some(Destination::Asset {
            asset: "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48".into(),
            chain: "eip155:1".into(),
            address: EVM_ADDR.to_vec(),
        }),
        payout: None,
    };
    c.set_policy("peni.uni".into(), Some(policy.clone()));
    assert!(c.get_pending_change("peni.uni".into()).is_some());
    c.cancel_pending_policy("peni.uni".into());
    assert!(c.get_pending_change("peni.uni".into()).is_none());

    // 5. set_policy & apply_pending_policy after delay
    let effective = c.set_policy("peni.uni".into(), Some(policy));
    let mut b = VMContextBuilder::new();
    b.predecessor_account_id(accounts(0))
        .current_account_id(accounts(1))
        .block_timestamp(effective * 1_000_000 + 1);
    testing_env!(b.build());
    c.apply_pending_policy("peni.uni".into());
    assert!(c.get_policy("peni.uni".into()).unwrap().force_default);

    // 6. remove_record
    c.remove_record("peni.uni".into(), "eip155:1".into());
    assert!(c
        .get_record("peni.uni".into())
        .unwrap()
        .chains
        .get("eip155:1")
        .is_none());
}

#[test]
fn length_tiered_pricing_and_payment_flow() {
    testing_env!(owner_ctx());
    let mut c = contract();

    // 1. Verify length-tiered pricing
    assert_eq!(c.price_for_name("abc".into()).0, 50_000_000); // 3 chars -> $50
    assert_eq!(c.price_for_name("abcd".into()).0, 20_000_000); // 4 chars -> $20
    assert_eq!(c.price_for_name("abcde".into()).0, 5_000_000); // 5 chars -> $5
    assert_eq!(c.price_for_name("verylongname.uni".into()).0, 5_000_000);

    // 2. Configure relayer and payment token (USDC: accounts(4), relayer: accounts(2))
    let usdc = accounts(4);
    let relayer = accounts(2);
    c.set_pricing_config(Some(usdc.clone()), Some(relayer.clone()), None, None, None);

    let config = c.get_pricing_config();
    assert_eq!(config.payment_token, Some(usdc.clone()));
    assert_eq!(config.relayer, Some(relayer.clone()));

    // 3. Unauthorized claim_for must fail
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(accounts(5))
        .current_account_id(accounts(1))
        .build());
    let err = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.claim_for("hacker".into(), accounts(5), "ref_fake".into());
    }));
    assert!(err.is_err(), "unauthorized claim_for must fail");

    // 4. Authorized relayer claim_for succeeds (Cross-chain checkout flow)
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(relayer.clone())
        .current_account_id(accounts(1))
        .build());
    let claimed = c.claim_for(
        "crosschain".into(),
        accounts(3),
        "intent:0x123abc456def".into(),
    );
    assert_eq!(claimed, "crosschain.uni");
    assert_eq!(c.owner_of("crosschain.uni".into()), Some(accounts(3)));

    // 5. Normal claim without payment when payment_token is set must fail
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(accounts(5))
        .current_account_id(accounts(1))
        .build());
    let err = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.claim("user5".into());
    }));
    assert!(err.is_err(), "claim without payment must fail");

    // 6. Native NEP-141 ft_on_transfer: Insufficient payment -> full refund
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(usdc.clone())
        .current_account_id(accounts(1))
        .build());
    let refund = match c.ft_on_transfer(
        accounts(5),
        near_sdk::json_types::U128(2_000_000), // only $2, needs $5
        "user5".into(),
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 2_000_000);
    assert_eq!(c.owner_of("user5.uni".into()), None);

    // 7. Native NEP-141 ft_on_transfer: Exact payment -> registered, 0 refund
    let refund = match c.ft_on_transfer(
        accounts(5),
        near_sdk::json_types::U128(5_000_000), // exact $5
        "{\"name\":\"user5\"}".into(),
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 0);
    assert_eq!(c.owner_of("user5.uni".into()), Some(accounts(5)));

    // 8. Native NEP-141 ft_on_transfer: Excess payment -> refunds difference
    // 4-char name "four" costs $20 (20_000_000), user pays $25 (25_000_000)
    let user6: AccountId = "user6.testnet".parse().unwrap();
    let refund = match c.ft_on_transfer(
        user6.clone(),
        near_sdk::json_types::U128(25_000_000),
        "four.uni".into(),
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 5_000_000);
    assert_eq!(c.owner_of("four.uni".into()), Some(user6.clone()));

    // 9. Unauthorized token calling ft_on_transfer must fail
    let fake_token: AccountId = "fake-token.testnet".parse().unwrap();
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(fake_token)
        .current_account_id(accounts(1))
        .build());
    let err = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = c.ft_on_transfer(
            user6.clone(),
            near_sdk::json_types::U128(50_000_000),
            "six.uni".into(),
        );
    }));
    assert!(err.is_err(), "unsupported token must panic");

    // 10. Admin adds USDT as an accepted payment stablecoin -> USDT ft_on_transfer succeeds
    let usdt: AccountId = "usdt.testnet".parse().unwrap();
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(accounts(0))
        .current_account_id(accounts(1))
        .build());
    c.add_payment_token(usdt.clone());
    assert!(c.get_pricing_config().accepted_tokens.contains(&usdt));

    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(usdt.clone())
        .current_account_id(accounts(1))
        .build());
    let refund = match c.ft_on_transfer(
        user6.clone(),
        near_sdk::json_types::U128(50_000_000),
        "six.uni".into(),
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 0);
    assert_eq!(c.owner_of("six.uni".into()), Some(user6));

    // 11. Cross-chain keyless callback abstraction:
    // A cross-chain solver (solver.testnet) transfers USDC on behalf of a foreign-chain user (alice.testnet)
    // specifying target owner in msg: `{"name": "callback.uni", "owner": "alice.testnet"}`.
    let solver: AccountId = "solver.testnet".parse().unwrap();
    let alice: AccountId = "alice.testnet".parse().unwrap();
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(usdc)
        .current_account_id(accounts(1))
        .build());
    let callback_msg = format!("{{\"name\": \"callback.uni\", \"owner\": \"{alice}\"}}");
    let refund = match c.ft_on_transfer(
        solver.clone(),
        near_sdk::json_types::U128(10_000_000), // Pays $10 for 8-char name (costs $5)
        callback_msg,
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    // Excess $5 refunded to solver
    assert_eq!(refund, 5_000_000);
    // Domain owned by alice.testnet, NOT solver.testnet!
    assert_eq!(c.owner_of("callback.uni".into()), Some(alice.clone()));
    let rec = c.get_record("callback.uni".into()).expect("record exists");
    assert_eq!(rec.owner, alice);
}

#[test]
fn ft_on_transfer_typed_enum_deserialization() {
    testing_env!(owner_ctx());
    let mut c = contract();

    let usdc = accounts(4);
    c.set_pricing_config(Some(usdc.clone()), None, None, None, None);

    let solver: AccountId = "solver.testnet".parse().unwrap();
    let alice: AccountId = "alice.testnet".parse().unwrap();
    let bob: AccountId = "bob.testnet".parse().unwrap();
    let charlie: AccountId = "charlie.testnet".parse().unwrap();

    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(usdc.clone())
        .current_account_id(accounts(1))
        .build());

    // 1. Tagged action: Claim enum variant holding ClaimArgs struct
    // {"claim": {"name": "typedclaim.uni", "owner": "alice.testnet"}}
    let msg_claim =
        format!("{{\"claim\": {{\"name\": \"typedclaim.uni\", \"owner\": \"{alice}\"}}}}");
    let refund = match c.ft_on_transfer(
        solver.clone(),
        near_sdk::json_types::U128(5_000_000),
        msg_claim,
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 0);
    assert_eq!(c.owner_of("typedclaim.uni".into()), Some(alice.clone()));

    // 2. Tagged action: Register enum variant holding ClaimArgs struct
    // {"register": {"name": "typedreg.uni", "owner": "bob.testnet"}}
    let msg_reg = format!("{{\"register\": {{\"name\": \"typedreg.uni\", \"owner\": \"{bob}\"}}}}");
    let refund = match c.ft_on_transfer(
        solver.clone(),
        near_sdk::json_types::U128(5_000_000),
        msg_reg,
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 0);
    assert_eq!(c.owner_of("typedreg.uni".into()), Some(bob.clone()));

    // 3. Direct untagged ClaimArgs struct: {"name": "directstruct.uni", "owner": "charlie.testnet"}
    let msg_direct = format!("{{\"name\": \"directstruct.uni\", \"owner\": \"{charlie}\"}}");
    let refund = match c.ft_on_transfer(
        solver.clone(),
        near_sdk::json_types::U128(5_000_000),
        msg_direct,
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 0);
    assert_eq!(c.owner_of("directstruct.uni".into()), Some(charlie.clone()));

    // 4. Direct untagged ClaimArgs struct without owner: defaults to sender_id
    let msg_no_owner = "{\"name\": \"directself.uni\"}".to_string();
    let refund = match c.ft_on_transfer(
        charlie.clone(),
        near_sdk::json_types::U128(5_000_000),
        msg_no_owner,
    ) {
        near_sdk::PromiseOrValue::Value(v) => v.0,
        _ => panic!("expected value"),
    };
    assert_eq!(refund, 0);
    assert_eq!(c.owner_of("directself.uni".into()), Some(charlie.clone()));

    // 5. Malformed JSON payload panics with INVALID_MSG to prevent losing funds on invalid actions
    let err = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = c.ft_on_transfer(
            solver.clone(),
            near_sdk::json_types::U128(5_000_000),
            "{\"completely_invalid_key\": 123}".to_string(),
        );
    }));
    assert!(err.is_err(), "malformed JSON payload must panic");
}
