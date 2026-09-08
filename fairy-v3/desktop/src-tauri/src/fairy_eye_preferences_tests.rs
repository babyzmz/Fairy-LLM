//! Additive schema-11 compatibility. Run with cargo test fairy_eye_preferences_tests.
use super::*;

#[test]
fn old_preferences_default_to_liquid_without_resetting_any_existing_value() {
    let mut before = DesktopPreferences::default();
    before.revision = 91;
    before.voice_replies_enabled = false;
    before.pet_optics_mode = PetOpticsMode::Enhanced;
    before.pet_size_percent = 125;
    before.pet_opacity_percent = 84;
    before.selected_profile_id = Some("keep-provider".to_owned());
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
    let mut preferences = DesktopPreferences::default();
    preferences.pet_form = PetForm::HddEye;
    preferences.chat_fairy_eye_enabled = false;
    preferences.pet_optics_mode = PetOpticsMode::Enhanced;
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
