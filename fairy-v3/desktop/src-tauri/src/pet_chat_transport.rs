use serde_json::{json, Value};

pub fn request(method: &str, params: Value) -> Value {
    // The Bridge accepts integer client IDs and supplies its own generation +
    // sequence identity on the wire. The host must not manufacture UUID IDs.
    json!({"jsonrpc": "2.0", "id": 0, "method": method, "params": params})
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pet_chat_broker::{PetChatBindingInput, PetChatBroker, PetModelSelection};
    use fairy_core_bridge::{CoreBridge, CoreLaunchSpec};
    use std::path::PathBuf;
    use uuid::Uuid;

    #[test]
    fn real_core_host_requests_bind_two_scratch_chats_without_provider_startup() {
        let core_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core");
        let data = tempfile::tempdir().unwrap();
        let bridge =
            CoreBridge::spawn_verified(CoreLaunchSpec::development(&core_root, data.path()))
                .unwrap();
        let preference = bridge
            .call(request("models.selection.get", json!({})))
            .unwrap();
        assert!(preference.get("error").is_none(), "{preference}");
        let selection = PetModelSelection::from_preference(&preference["result"]).unwrap();
        let broker = PetChatBroker::default();
        for revision in 0..2 {
            let created = bridge
                .call(request(
                    "conversations.create",
                    json!({
                        "project_id": null, "workspace_type": "chat_scratch",
                    }),
                ))
                .unwrap();
            assert!(created.get("error").is_none(), "{created}");
            let id: Uuid = serde_json::from_value(created["result"]["id"].clone()).unwrap();
            let conversation = bridge
                .call(request(
                    "conversations.get",
                    json!({
                        "conversation_id": id,
                    }),
                ))
                .unwrap();
            let context = broker
                .bind(
                    PetChatBindingInput {
                        expected_revision: revision,
                        conversation_id: id,
                        profile_id: None,
                        model_selection: Some(selection.clone()),
                    },
                    &conversation["result"],
                )
                .unwrap();
            assert_eq!(context.conversation_id, Some(id));
            assert_eq!(context.revision, revision + 1);
        }
    }
}
