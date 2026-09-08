//! Additive schema-11 compatibility. Run with cargo test fairy_eye_preferences_tests.
use super::*;

#[test]
fn old_preferences_default_to_liquid_without_resetting_any_existing_value() {
    let before = DesktopPreferences {
        revision: 91,
        voice_replies_enabled: false,
        pet_optics_mode: PetOpticsMode::Enhanced,
        pet_size_percent: 125,
        pet_opacity_percent: 84,
        selected_profile_id: Some("keep-provider".to_owned()),
        ..DesktopPreferences::default()
    };
    let mut old = serde_json::to_value(&before).unwrap();
    old.as_object_mut().unwrap().remove("pet_form");
    old.as_object_mut().unwrap().remove("chat_fairy_eye_enabled");
    let loaded: DesktopPreferences = serde_json::from_value(old.clone()).unwrap();
    assert_eq!(loaded.pet_form, PetForm::LiquidGlass);
    assert!(loaded.chat_fairy_eye_enabled);
    assert_eq!(loaded.schema_version, 11);
    let mut roundtrip = serde_json::to_value(loaded).unwrap();
    roundtrip.as_object_mut().unwrap().remove("pet_form");
    roundtrip.as_object_mut().unwrap().remove("chat_fairy_eye_enabled");
    assert_eq!(old, roundtrip);
}

#[test]
fn form_and_chat_visibility_roundtrip_independently() {
    let preferences = DesktopPreferences {
        pet_form: PetForm::HddEye,
        chat_fairy_eye_enabled: false,
        pet_optics_mode: PetOpticsMode::Enhanced,
        ..DesktopPreferences::default()
    };
    let loaded: DesktopPreferences = serde_json::from_slice(&serde_json::to_vec(&preferences).unwrap()).unwrap();
    assert_eq!(loaded.pet_form, PetForm::HddEye);
    assert!(!loaded.chat_fairy_eye_enabled);
    assert_eq!(loaded.pet_optics_mode, PetOpticsMode::Enhanced);
    assert_eq!(loaded, preferences);
}

#[test]
fn malformed_form_is_rejected_not_silently_replaced_with_a_default_profile() {
    let mut value = serde_json::to_value(DesktopPreferences::default()).unwrap();
    value["pet_form"] = serde_json::json!("unknown-form");
    assert!(serde_json::from_value::<DesktopPreferences>(value).is_err());
}
