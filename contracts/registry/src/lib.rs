use near_sdk::serde::Serialize;
use near_sdk::serde_json::json;
use near_sdk::store::{IterableSet, LookupMap};
use near_sdk::{env, near, require, AccountId, PanicOnDefault};

pub const NAME_MIN: usize = 3;
pub const NAME_MAX: usize = 32;
pub const DEFAULT_DELAY_SECS: u64 = 3600;

const RESERVED: [&str; 16] = [
    "admin",
    "root",
    "system",
    "near",
    "uni",
    "registry",
    "registrar",
    "www",
    "api",
    "app",
    "support",
    "help",
    "security",
    "official",
    "mail",
    "null",
];

pub const NS_EIP155: &str = "eip155";
pub const NS_SOLANA: &str = "solana";
pub const NS_BIP122: &str = "bip122";
pub const NS_NEAR: &str = "near";

pub const COIN_TYPE_SOLANA: u64 = 501;
pub const COIN_TYPE_BIP122: u64 = 0;
pub const COIN_TYPE_NEAR: u64 = 397;
const EVM_FLAG: u64 = 0x8000_0000;

fn panic_code(code: &str, detail: &str) -> ! {
    if detail.is_empty() {
        env::panic_str(code)
    } else {
        env::panic_str(&format!("{code}: {detail}"))
    }
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub struct ChainRecord {
    pub address: Vec<u8>,
    pub proof: Vec<u8>,
    pub set_at_ms: u64,
    pub generation: u32,
    pub coin_type: u64,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub enum Destination {
    Asset {
        asset: String,
        chain: String,
        address: Vec<u8>,
    },
    Payout {
        provider: String,
        reference: String,
    },
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub struct ReceivePolicy {
    pub force_default: bool,
    pub default: Option<Destination>,
    pub payout: Option<Destination>,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub enum PendingChangeKind {
    Set(ReceivePolicy),
    Clear,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub struct PendingChange {
    pub effective_at_ms: u64,
    pub kind: PendingChangeKind,
}

#[near(serializers = [borsh, json])]
#[derive(Debug, Clone)]
pub struct Record {
    pub owner: AccountId,
    pub generation: u32,
    pub chains: std::collections::BTreeMap<String, ChainRecord>,
    pub policy: Option<ReceivePolicy>,
    pub pending: Option<PendingChange>,
}

#[near(serializers = [borsh, json])]
#[derive(Debug, Clone)]
pub struct ResolvedRecord {
    pub address: Vec<u8>,
    pub coin_type: u64,
    pub set_at_ms: u64,
    pub generation: u32,
    pub chain: String,
}

#[derive(Serialize, Clone, Debug)]
#[serde(untagged)]
pub enum EventData {
    NameClaimed {
        name: String,
        owner: String,
    },
    ChainConnected {
        name: String,
        chain: String,
        address: String,
    },
    ChainDisconnected {
        name: String,
        chain: String,
    },
    PolicyChangeQueued {
        name: String,
        effective_at_ms: u64,
    },
    PolicyChanged {
        name: String,
    },
    PolicyChangeCancelled {
        name: String,
    },
    NameTransferred {
        name: String,
        from: String,
        to: String,
    },
}

fn emit(event: &str, data: &impl Serialize) {
    env::log_str(
        &json!({
            "standard": "unam-1.0.0",
            "version": "1.0.0",
            "event": event,
            "data": data,
        })
        .to_string(),
    );
}

pub type ValResult<T> = Result<T, (&'static str, String)>;

pub fn validate_name_checked(raw: &str) -> ValResult<String> {
    let lowered = raw.trim().to_ascii_lowercase();
    let label = lowered.strip_suffix(".uni").unwrap_or(&lowered);
    if label.len() < NAME_MIN || label.len() > NAME_MAX {
        return Err(("NAME_INVALID", "length must be 3..=32".into()));
    }
    if !label
        .bytes()
        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    {
        return Err(("NAME_INVALID", "only a-z, 0-9 and '-' allowed".into()));
    }
    if label.starts_with('-') || label.ends_with('-') || label.contains("--") {
        return Err(("NAME_INVALID", "bad '-' placement".into()));
    }
    if RESERVED.contains(&label) {
        return Err(("NAME_INVALID", "reserved name".into()));
    }
    Ok(label.to_string())
}

pub fn validate_name(raw: &str) -> String {
    validate_name_checked(raw).unwrap_or_else(|(code, detail)| panic_code(code, &detail))
}

pub fn canonical_name_checked(raw: &str) -> ValResult<String> {
    let label = validate_name_checked(raw)?;
    Ok(format!("{label}.uni"))
}

pub fn canonical_name(raw: &str) -> String {
    canonical_name_checked(raw).unwrap_or_else(|(code, detail)| panic_code(code, &detail))
}

pub fn parse_chain_key_checked(chain: &str) -> ValResult<(String, String, u64)> {
    let (ns, rest) = chain
        .split_once(':')
        .ok_or(("CHAIN_KEY_INVALID", "expected namespace:reference".into()))?;
    let ns = ns.to_ascii_lowercase();
    if rest.is_empty() {
        return Err(("CHAIN_KEY_INVALID", "empty reference".into()));
    }
    let coin_type = match ns.as_str() {
        NS_EIP155 => {
            let id: u64 = rest.parse().map_err(|_| {
                (
                    "CHAIN_KEY_INVALID",
                    "eip155 reference must be numeric".into(),
                )
            })?;
            if id >= EVM_FLAG {
                return Err(("CHAIN_KEY_INVALID", "chainId too large".into()));
            }
            EVM_FLAG | id
        }
        NS_SOLANA => {
            if rest.len() < 32
                || rest.len() > 44
                || !rest
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            {
                return Err((
                    "CHAIN_KEY_INVALID",
                    "solana reference must be 32..44 base58 chars".into(),
                ));
            }
            COIN_TYPE_SOLANA
        }
        NS_BIP122 => {
            if rest.len() != 32 && rest.len() != 64 {
                return Err((
                    "CHAIN_KEY_INVALID",
                    "bip122 reference must be 32 or 64 hex chars (genesis hash prefix)".into(),
                ));
            }
            if !rest.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err(("CHAIN_KEY_INVALID", "bip122 reference must be hex".into()));
            }
            COIN_TYPE_BIP122
        }
        NS_NEAR => {
            if rest != "mainnet" && rest != "testnet" {
                return Err((
                    "CHAIN_KEY_INVALID",
                    "near reference must be mainnet|testnet".into(),
                ));
            }
            COIN_TYPE_NEAR
        }
        other => {
            return Err((
                "CHAIN_KEY_INVALID",
                format!("unsupported namespace: {other}"),
            ))
        }
    };
    Ok((ns, rest.to_string(), coin_type))
}

pub fn parse_chain_key(chain: &str) -> (String, String, u64) {
    parse_chain_key_checked(chain).unwrap_or_else(|(code, detail)| panic_code(code, &detail))
}

pub fn normalize_chain_key(chain: &str) -> String {
    let (ns, rest, _) = parse_chain_key(chain);
    format!("{ns}:{rest}")
}

pub fn validate_address_checked(ns: &str, address: &[u8]) -> ValResult<()> {
    let ok = match ns {
        NS_EIP155 => address.len() == 20,
        NS_SOLANA => address.len() == 32,
        NS_BIP122 => (20..=80).contains(&address.len()),
        NS_NEAR => {
            address.len() >= 2
                && address.len() <= 64
                && std::str::from_utf8(address).is_ok_and(|s| {
                    s.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-' || b == b'_')
                })
        }
        _ => false,
    };
    if ok {
        Ok(())
    } else {
        Err(("ADDRESS_INVALID", format!("bad address for namespace {ns}")))
    }
}

pub fn validate_address(ns: &str, address: &[u8]) {
    validate_address_checked(ns, address)
        .unwrap_or_else(|(code, detail)| panic_code(code, &detail));
}

pub fn validate_policy_checked(policy: &ReceivePolicy) -> ValResult<()> {
    match (&policy.default, &policy.payout) {
        (None, None) => Err(("POLICY_INVALID", "must set default or payout".into())),
        (
            Some(Destination::Asset {
                chain,
                address,
                asset,
            }),
            _,
        ) => {
            let (ns, _, _) = parse_chain_key_checked(chain)?;
            validate_address_checked(&ns, address)?;
            if asset.is_empty() {
                return Err(("POLICY_INVALID", "empty asset id".into()));
            }
            Ok(())
        }
        (Some(Destination::Payout { .. }), _) => {
            Err(("POLICY_INVALID", "default route must be Asset".into()))
        }
        (
            None,
            Some(Destination::Payout {
                provider,
                reference,
            }),
        ) => {
            if provider.is_empty() || reference.is_empty() {
                return Err((
                    "POLICY_INVALID",
                    "payout provider/reference must be non-empty".into(),
                ));
            }
            if !provider
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
            {
                return Err((
                    "POLICY_INVALID",
                    "provider must be alphanumeric-dash".into(),
                ));
            }
            Ok(())
        }
        (None, Some(Destination::Asset { .. })) => {
            Err(("POLICY_INVALID", "payout route must be Payout".into()))
        }
    }
}

pub fn validate_policy(policy: &ReceivePolicy) {
    validate_policy_checked(policy).unwrap_or_else(|(code, detail)| panic_code(code, &detail));
}

fn owner_prefix(owner: &AccountId) -> Vec<u8> {
    format!("o/{owner}").into_bytes()
}

fn require_owner(record: &Record) {
    require!(record.owner == env::predecessor_account_id(), "NOT_OWNER");
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Contract {
    records: LookupMap<String, Record>,
    owner_names: LookupMap<AccountId, IterableSet<String>>,
    change_delay_ms: u64,
}

#[near]
impl Contract {
    #[init]
    pub fn new(change_delay_secs: Option<u64>) -> Self {
        Self {
            records: LookupMap::new(b"r".to_vec()),
            owner_names: LookupMap::new(b"n".to_vec()),
            change_delay_ms: change_delay_secs.unwrap_or(DEFAULT_DELAY_SECS) * 1000,
        }
    }

    pub fn change_delay_ms(&self) -> u64 {
        self.change_delay_ms
    }

    pub fn claim(&mut self, name: String) -> String {
        let full = canonical_name(&name);
        if self.records.contains_key(&full) {
            panic_code("NAME_TAKEN", "");
        }
        let owner = env::predecessor_account_id();
        self.records.insert(
            full.clone(),
            Record {
                owner: owner.clone(),
                generation: 1,
                chains: std::collections::BTreeMap::new(),
                policy: None,
                pending: None,
            },
        );
        match self.owner_names.get_mut(&owner) {
            Some(set) => {
                set.insert(full.clone());
            }
            None => {
                let mut set = IterableSet::new(owner_prefix(&owner));
                set.insert(full.clone());
                self.owner_names.insert(owner.clone(), set);
            }
        }
        emit(
            "NameClaimed",
            &EventData::NameClaimed {
                name: full.clone(),
                owner: owner.to_string(),
            },
        );
        full
    }

    pub fn set_record(&mut self, name: String, chain: String, address: Vec<u8>, proof: Vec<u8>) {
        let full = canonical_name(&name);
        let (ns, rest, coin_type) = parse_chain_key(&chain);
        validate_address(&ns, &address);
        if proof.is_empty() {
            panic_code("ADDRESS_INVALID", "empty proof");
        }
        let record = self
            .records
            .get_mut(&full)
            .unwrap_or_else(|| panic_code("NAME_NOT_FOUND", ""));
        require_owner(record);
        let key = format!("{ns}:{rest}");
        record.chains.insert(
            key.clone(),
            ChainRecord {
                address: address.clone(),
                proof,
                set_at_ms: env::block_timestamp_ms(),
                generation: record.generation,
                coin_type,
            },
        );
        emit(
            "ChainConnected",
            &EventData::ChainConnected {
                name: full,
                chain: key,
                address: hex::encode(address),
            },
        );
    }

    pub fn connect_chain(&mut self, name: String, chain: String, address: Vec<u8>, proof: Vec<u8>) {
        self.set_record(name, chain, address, proof);
    }

    pub fn remove_record(&mut self, name: String, chain: String) {
        let full = canonical_name(&name);
        let key = normalize_chain_key(&chain);
        let record = self
            .records
            .get_mut(&full)
            .unwrap_or_else(|| panic_code("NAME_NOT_FOUND", ""));
        require_owner(record);
        if record.chains.remove(&key).is_none() {
            panic_code("NO_RECORD", "");
        }
        emit(
            "ChainDisconnected",
            &EventData::ChainDisconnected {
                name: full,
                chain: key,
            },
        );
    }

    pub fn disconnect_chain(&mut self, name: String, chain: String) {
        self.remove_record(name, chain);
    }

    pub fn set_policy(&mut self, name: String, policy: Option<ReceivePolicy>) -> u64 {
        let full = canonical_name(&name);
        if let Some(p) = &policy {
            validate_policy(p);
        }
        let delay = self.change_delay_ms;
        let record = self
            .records
            .get_mut(&full)
            .unwrap_or_else(|| panic_code("NAME_NOT_FOUND", ""));
        require_owner(record);
        let effective_at_ms = env::block_timestamp_ms() + delay;
        record.pending = Some(PendingChange {
            effective_at_ms,
            kind: match policy {
                Some(p) => PendingChangeKind::Set(p),
                None => PendingChangeKind::Clear,
            },
        });
        emit(
            "PolicyChangeQueued",
            &EventData::PolicyChangeQueued {
                name: full,
                effective_at_ms,
            },
        );
        effective_at_ms
    }

    pub fn set_receive_policy(&mut self, name: String, policy: Option<ReceivePolicy>) -> u64 {
        self.set_policy(name, policy)
    }

    pub fn apply_pending_policy(&mut self, name: String) {
        self.apply_pending_change(name);
    }

    pub fn cancel_pending_policy(&mut self, name: String) {
        self.cancel_pending_change(name);
    }

    pub fn apply_pending_change(&mut self, name: String) {
        let full = canonical_name(&name);
        let record = self
            .records
            .get_mut(&full)
            .unwrap_or_else(|| panic_code("NAME_NOT_FOUND", ""));
        let pending = match &record.pending {
            Some(p) => p,
            None => panic_code("NO_PENDING_CHANGE", ""),
        };
        if env::block_timestamp_ms() < pending.effective_at_ms {
            panic_code("CHANGE_PENDING", &pending.effective_at_ms.to_string());
        }
        let pending = record.pending.take().unwrap();
        record.policy = match pending.kind {
            PendingChangeKind::Set(p) => Some(p),
            PendingChangeKind::Clear => None,
        };
        emit("PolicyChanged", &EventData::PolicyChanged { name: full });
    }

    pub fn cancel_pending_change(&mut self, name: String) {
        let full = canonical_name(&name);
        let record = self
            .records
            .get_mut(&full)
            .unwrap_or_else(|| panic_code("NAME_NOT_FOUND", ""));
        require_owner(record);
        if record.pending.is_none() {
            panic_code("NO_PENDING_CHANGE", "");
        }
        record.pending = None;
        emit(
            "PolicyChangeCancelled",
            &EventData::PolicyChangeCancelled { name: full },
        );
    }

    pub fn transfer_name(&mut self, name: String, new_owner: AccountId) {
        let full = canonical_name(&name);
        let old_owner = {
            let record = self
                .records
                .get_mut(&full)
                .unwrap_or_else(|| panic_code("NAME_NOT_FOUND", ""));
            require_owner(record);
            if new_owner == record.owner {
                panic_code("SELF_TRANSFER", "");
            }
            let old = record.owner.clone();
            record.owner = new_owner.clone();
            record.generation += 1;
            record.pending = None;
            let gen = record.generation;
            for c in record.chains.values_mut() {
                c.generation = gen;
            }
            old
        };

        let old_index_empty = match self.owner_names.get_mut(&old_owner) {
            Some(set) => {
                if !set.remove(&full) {
                    panic_code("NAME_NOT_FOUND", "owner index missing name");
                }
                set.is_empty()
            }
            None => panic_code("NAME_NOT_FOUND", "owner index missing"),
        };
        if old_index_empty {
            self.owner_names.remove(&old_owner);
        }
        match self.owner_names.get_mut(&new_owner) {
            Some(set) => {
                set.insert(full.clone());
            }
            None => {
                let mut set = IterableSet::new(owner_prefix(&new_owner));
                set.insert(full.clone());
                self.owner_names.insert(new_owner.clone(), set);
            }
        }

        emit(
            "NameTransferred",
            &EventData::NameTransferred {
                name: full,
                from: old_owner.to_string(),
                to: new_owner.to_string(),
            },
        );
    }

    pub fn resolve_direct(&self, name: String, chain: String) -> ResolvedRecord {
        let full = canonical_name(&name);
        let key = normalize_chain_key(&chain);
        let record = match self.records.get(&full) {
            Some(r) => r,
            None => panic_code("NAME_NOT_FOUND", ""),
        };
        let c = record
            .chains
            .get(&key)
            .unwrap_or_else(|| panic_code("NO_RECORD", ""));
        ResolvedRecord {
            address: c.address.clone(),
            coin_type: c.coin_type,
            set_at_ms: c.set_at_ms,
            generation: c.generation,
            chain: key,
        }
    }

    pub fn get_record(&self, name: String) -> Option<Record> {
        let full = canonical_name(&name);
        self.records.get(&full).cloned()
    }

    pub fn get_name(&self, name: String) -> Option<Record> {
        self.get_record(name)
    }

    pub fn get_policy(&self, name: String) -> Option<ReceivePolicy> {
        let full = canonical_name(&name);
        self.records.get(&full).and_then(|r| r.policy.clone())
    }

    pub fn get_pending_change(&self, name: String) -> Option<PendingChange> {
        let full = canonical_name(&name);
        self.records.get(&full).and_then(|r| r.pending.clone())
    }

    pub fn owner_of(&self, name: String) -> Option<AccountId> {
        let full = canonical_name(&name);
        self.records.get(&full).map(|r| r.owner.clone())
    }

    pub fn is_name_available(&self, name: String) -> bool {
        let full = canonical_name(&name);
        !self.records.contains_key(&full)
    }

    pub fn names_of(
        &self,
        account: AccountId,
        skip: Option<usize>,
        limit: Option<usize>,
    ) -> Vec<String> {
        let s = skip.unwrap_or(0);
        let l = limit.unwrap_or(10).min(100);
        match self.owner_names.get(&account) {
            Some(set) => set.iter().skip(s).take(l).cloned().collect(),
            None => vec![],
        }
    }

    pub fn records_of(&self, owner: AccountId, skip: usize, limit: usize) -> Vec<String> {
        self.names_of(owner, Some(skip), Some(limit))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn name_validation_accepts_and_rejects() {
        assert_eq!(validate_name_checked("peni").unwrap(), "peni");
        assert_eq!(validate_name_checked("Peni.Uni").unwrap(), "peni");
        assert_eq!(validate_name_checked("  peni  ").unwrap(), "peni");
        assert_eq!(validate_name_checked("UPPER").unwrap(), "upper");
        for bad in ["ab", "-peni", "peni-", "pe--ni", "p ni", "admin", ""] {
            assert!(
                validate_name_checked(bad).is_err(),
                "expected rejection of {bad:?}"
            );
        }
    }

    #[test]
    fn chain_key_and_cointype() {
        let (_, _, c) = parse_chain_key_checked("eip155:1").unwrap();
        assert_eq!(c, EVM_FLAG | 1);
        let (_, _, c) =
            parse_chain_key_checked("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d").unwrap();
        assert_eq!(c, COIN_TYPE_SOLANA);
        let (_, _, c) = parse_chain_key_checked("bip122:000000000019d6689c085ae165831e93").unwrap();
        assert_eq!(c, COIN_TYPE_BIP122);
        let (_, _, c) = parse_chain_key_checked("near:testnet").unwrap();
        assert_eq!(c, COIN_TYPE_NEAR);
        for bad in [
            "ethereum:1",
            "eip155:abc",
            "solana:short",
            "bip122:xyz",
            "near:devnet",
        ] {
            assert!(
                parse_chain_key_checked(bad).is_err(),
                "expected rejection of {bad:?}"
            );
        }
    }

    #[test]
    fn address_lengths_per_namespace() {
        assert!(validate_address_checked("eip155", &[0u8; 20]).is_ok());
        assert!(validate_address_checked("eip155", &[0u8; 32]).is_err());
        assert!(validate_address_checked("solana", &[0u8; 32]).is_ok());
        assert!(validate_address_checked("solana", &[0u8; 20]).is_err());
        assert!(validate_address_checked("bip122", &[0u8; 25]).is_ok());
        assert!(validate_address_checked("bip122", &[0u8; 10]).is_err());
        assert!(validate_address_checked("near", b"alice.near").is_ok());
        assert!(validate_address_checked("near", b"a").is_err());
    }

    #[test]
    fn policy_validation() {
        let asset_policy = ReceivePolicy {
            force_default: false,
            default: Some(Destination::Asset {
                asset: "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48".into(),
                chain: "eip155:1".into(),
                address: vec![0u8; 20],
            }),
            payout: None,
        };
        assert!(validate_policy_checked(&asset_policy).is_ok());
        let payout_policy = ReceivePolicy {
            force_default: true,
            default: None,
            payout: Some(Destination::Payout {
                provider: "bastion".into(),
                reference: "ref-123".into(),
            }),
        };
        assert!(validate_policy_checked(&payout_policy).is_ok());
        let empty = ReceivePolicy {
            force_default: false,
            default: None,
            payout: None,
        };
        assert!(validate_policy_checked(&empty).is_err());
        let payout_as_default = ReceivePolicy {
            force_default: false,
            default: Some(Destination::Payout {
                provider: "bastion".into(),
                reference: "ref".into(),
            }),
            payout: None,
        };
        assert!(validate_policy_checked(&payout_as_default).is_err());
    }
}
