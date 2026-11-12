use serde_json::json;

const WASM_PATH: &str = "../../dist/unam_registry.wasm";

#[tokio::test]
async fn test_sandbox_vertical_slice() -> Result<(), Box<dyn std::error::Error>> {
    // If the wasm hasn't been built yet, compile it or read dist
    let wasm = match std::fs::read(WASM_PATH) {
        Ok(bytes) => bytes,
        Err(_) => {
            // fallback to relative from crate root
            std::fs::read("dist/unam_registry.wasm")
                .or_else(|_| std::fs::read("../dist/unam_registry.wasm"))?
        }
    };

    let worker = match near_workspaces::sandbox().await {
        Ok(w) => w,
        Err(e) => {
            eprintln!("Skipping sandbox test: sandbox binary could not start ({e})");
            return Ok(());
        }
    };

    let contract = worker.dev_deploy(&wasm).await?;
    let alice = worker.dev_create_account().await?;

    // 1. Initialize contract
    let res = contract
        .call("new")
        .args_json(json!({ "change_delay_secs": 60 }))
        .transact()
        .await?;
    assert!(res.is_success());

    // 2. Query change_delay_ms
    let delay: u64 = contract
        .view("change_delay_ms")
        .args_json(json!({}))
        .await?
        .json()?;
    assert_eq!(delay, 60_000);

    // 3. Claim name
    let name: String = alice
        .call(contract.id(), "claim")
        .args_json(json!({ "name": "peni" }))
        .transact()
        .await?
        .json()?;
    assert_eq!(name, "peni.uni");

    // 4. Connect chains (EVM with set_record, Solana with connect_chain)
    let evm_addr = vec![0x11u8; 20];
    let sol_addr = vec![0x22u8; 32];

    let connect_res = alice
        .call(contract.id(), "set_record")
        .args_json(json!({
            "name": "peni.uni",
            "chain": "eip155:1",
            "address": evm_addr,
            "proof": [1, 2, 3]
        }))
        .transact()
        .await?;
    assert!(connect_res.is_success());

    let connect_sol = alice
        .call(contract.id(), "connect_chain")
        .args_json(json!({
            "name": "peni.uni",
            "chain": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
            "address": sol_addr,
            "proof": [4, 5, 6]
        }))
        .transact()
        .await?;
    assert!(connect_sol.is_success());

    // 5. Resolve direct EVM
    let resolved: serde_json::Value = contract
        .view("resolve_direct")
        .args_json(json!({
            "name": "peni.uni",
            "chain": "eip155:1"
        }))
        .await?
        .json()?;
    assert_eq!(resolved["chain"], "eip155:1");
    assert_eq!(resolved["coin_type"], 0x8000_0000u64 | 1);
    assert_eq!(resolved["generation"], 1);

    // 6. Check owner_of
    let owner: Option<String> = contract
        .view("owner_of")
        .args_json(json!({ "name": "peni.uni" }))
        .await?
        .json()?;
    assert_eq!(owner, Some(alice.id().to_string()));

    // 7. Check names_of, records_of, and get_name
    let names: Vec<String> = contract
        .view("names_of")
        .args_json(json!({
            "account": alice.id(),
            "skip": 0,
            "limit": 10
        }))
        .await?
        .json()?;
    assert_eq!(names, vec!["peni.uni"]);

    let records: Vec<String> = contract
        .view("records_of")
        .args_json(json!({
            "owner": alice.id(),
            "skip": 0,
            "limit": 10
        }))
        .await?
        .json()?;
    assert_eq!(records, vec!["peni.uni"]);

    let rec: serde_json::Value = contract
        .view("get_name")
        .args_json(json!({ "name": "peni.uni" }))
        .await?
        .json()?;
    assert_eq!(rec["owner"], alice.id().to_string());

    // 8. Honest failure for unconnected chain
    let res = contract
        .view("resolve_direct")
        .args_json(json!({
            "name": "peni.uni",
            "chain": "bip122:000000000019d6689c085ae165831e93"
        }))
        .await;
    assert!(res.is_err(), "unconnected chain must return error");

    println!("Sandbox vertical slice completed successfully!");
    Ok(())
}
