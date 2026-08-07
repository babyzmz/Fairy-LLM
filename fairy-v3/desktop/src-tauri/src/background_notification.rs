use uuid::Uuid;

#[derive(Clone, Debug, serde::Deserialize)]
pub struct BackgroundTaskNotificationInput {
    pub title: String,
    pub body: String,
    pub conversation_id: String,
    pub turn_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatedBackgroundTaskNotification {
    pub title: String,
    pub body: String,
    pub conversation_id: String,
    pub turn_id: Option<String>,
}

impl BackgroundTaskNotificationInput {
    pub fn validate(self) -> Result<ValidatedBackgroundTaskNotification, String> {
        let conversation_id = canonical_uuid(&self.conversation_id, "Conversation")?;
        let turn_id = self
            .turn_id
            .map(|value| canonical_uuid(&value, "Turn"))
            .transpose()?;
        Ok(ValidatedBackgroundTaskNotification {
            title: canonical_text(&self.title, 120, "Notification title")?,
            body: canonical_text(&self.body, 240, "Notification body")?,
            conversation_id,
            turn_id,
        })
    }
}

#[cfg(target_os = "windows")]
pub fn show<F>(
    application_id: &str,
    notification: &ValidatedBackgroundTaskNotification,
    on_activated: F,
) -> Result<(), String>
where
    F: Fn() + Send + 'static,
{
    tauri_winrt_notification::Toast::new(application_id)
        .title("Fairy")
        .text1(&notification.title)
        .text2(&notification.body)
        .on_activated(move |_| {
            on_activated();
            Ok(())
        })
        .show()
        .map_err(|error| error.to_string())
}

#[cfg(not(target_os = "windows"))]
pub fn show<F>(
    _application_id: &str,
    _notification: &ValidatedBackgroundTaskNotification,
    _on_activated: F,
) -> Result<(), String>
where
    F: Fn() + Send + 'static,
{
    Err("Background task notifications are only available on Windows".to_owned())
}

fn canonical_uuid(value: &str, label: &str) -> Result<String, String> {
    Uuid::parse_str(value.trim())
        .map(|value| value.to_string())
        .map_err(|_| format!("{label} identifier is invalid"))
}

fn canonical_text(value: &str, maximum: usize, label: &str) -> Result<String, String> {
    let canonical = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if canonical.is_empty() || canonical.chars().count() > maximum {
        return Err(format!("{label} is invalid"));
    }
    Ok(canonical)
}

#[cfg(test)]
mod tests {
    use super::BackgroundTaskNotificationInput;

    #[test]
    fn notification_payload_is_bounded_and_canonical() {
        let notification = BackgroundTaskNotificationInput {
            title: "  Background   task completed ".to_owned(),
            body: "Chat\nsummary".to_owned(),
            conversation_id: "019f7b34-9300-7000-8000-000000000010".to_owned(),
            turn_id: Some("019f7b34-9300-7000-8000-000000000012".to_owned()),
        }
        .validate()
        .expect("valid notification");

        assert_eq!(notification.title, "Background task completed");
        assert_eq!(notification.body, "Chat summary");
        assert_eq!(
            notification.turn_id.as_deref(),
            Some("019f7b34-9300-7000-8000-000000000012")
        );
    }

    #[test]
    fn notification_payload_rejects_unbounded_or_invalid_fields() {
        let invalid = BackgroundTaskNotificationInput {
            title: "x".repeat(121),
            body: "public".to_owned(),
            conversation_id: "not-a-conversation".to_owned(),
            turn_id: None,
        };

        assert!(invalid.validate().is_err());
    }
}
