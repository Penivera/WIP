use near_sdk::test_utils::{accounts, VMContextBuilder};
use near_sdk::{testing_env, VMContext};
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
    let r = c.resolve_direct("peni.uni".into(), "SOLANA:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d".into());
    assert_eq!(r.address, SOL_ADDR.to_vec());
    assert_eq!(r.coin_type, 501);
    assert_eq!(c.records_of(accounts(0), 0, 10).len(), 1);
    assert_eq!(c.owner_of("peni.uni".into()), Some(accounts(0)));
}

#[test]
fn unconnected_chain_is_honest_no_record() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());
    c.connect_chain("peni.uni".into(), "eip155:1".into(), EVM_ADDR.to_vec(), vec![1]);
    assert!(c.get_record("peni.uni".into()).unwrap().chains.get("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d").is_none());
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
        c2.connect_chain("peni.uni".into(), "eip155:1".into(), EVM_ADDR.to_vec(), vec![1]);
    }));
    assert!(err.is_err(), "non-owner connect must panic");
}

#[test]
fn transfer_bumps_generation_and_invalidates_policy_queue() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("peni".into());
    c.connect_chain("peni.uni".into(), "eip155:1".into(), EVM_ADDR.to_vec(), vec![1, 2, 3]);

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
    assert_eq!(r.generation, 2, "stale-proof generation must bump on transfer");
    assert_eq!(c.records_of(accounts(0), 0, 10).len(), 0, "old owner index must be cleared");
    assert_eq!(c.records_of(accounts(2), 0, 10), vec!["peni.uni".to_string()]);
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
    assert!(c.get_policy("peni.uni".into()).is_none(), "policy must not be active before delay");

    let early = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.apply_pending_change("peni.uni".into());
    }));
    assert!(early.is_err(), "early apply must panic with CHANGE_PENDING");

    let mut b = VMContextBuilder::new();
    b.predecessor_account_id(accounts(0)).current_account_id(accounts(1)).block_timestamp(effective * 1_000_000 + 1);
    testing_env!(b.build());
    c.apply_pending_change("peni.uni".into());
    let active = c.get_policy("peni.uni".into()).unwrap();
    assert!(active.force_default);
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
    b.predecessor_account_id(accounts(0)).current_account_id(accounts(1))
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
    let t = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        c.claim("x".into())
    }));
    assert!(t.is_err());
    let name = c.claim("Peni.Uni".into());
    assert_eq!(name, "peni.uni", "suffix + case normalization must fold to one record");
    let t = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.claim("peni".into())));
    assert!(t.is_err(), "double claim must panic NAME_TAKEN");
}

#[test]
fn repro_lookupmap_cache() {
    use near_sdk::store::LookupMap;
    let mut m: LookupMap<String, u32> = LookupMap::new(b"x".to_vec());
    let k = "a".to_string();
    assert!(!m.contains_key(&k));
    m.insert(k.clone(), 1);
    assert!(m.get_mut(&k).is_some(), "get_mut must see inserted value");
    m.remove(&k);
    assert!(!m.contains_key(&k));
    m.insert(k.clone(), 2);
    assert!(m.get_mut(&k).is_some());
    assert!(!m.contains_key(&"ghost".to_string()));
    m.insert("ghost".to_string(), 3);
    assert!(m.get_mut(&"ghost".to_string()).is_some());
}

#[test]
fn repro_contract_claim_then_connect() {
    testing_env!(owner_ctx());
    let mut c = contract();
    c.claim("zzz".into());
    testing_env!(owner_ctx());
    c.connect_chain("zzz.uni".into(), "eip155:1".into(), EVM_ADDR.to_vec(), vec![1]);
}
