use std::path::Path;

use serde::{Deserialize, Serialize};

const MAX_EXCLUDED_APPLICATIONS: usize = 32;
const MAX_EXECUTABLE_BASENAME_BYTES: usize = 128;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeCaptureMode {
    #[default]
    SelectedWindow,
    FollowForeground,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeSensitiveCategory {
    SecureDesktop,
    FairyOwned,
    CredentialApplication,
    FinancialOrPrivate,
    ProtectedContent,
    UserExcluded,
}

impl RealtimeSensitiveCategory {
    pub const fn public_code(self) -> &'static str {
        "SENSITIVE_WINDOW_BLOCKED"
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RealtimeWindowDecision {
    pub source_id: Option<u64>,
    pub sensitive: Option<RealtimeSensitiveCategory>,
}

impl RealtimeWindowDecision {
    pub const fn unavailable() -> Self {
        Self {
            source_id: None,
            sensitive: Some(RealtimeSensitiveCategory::SecureDesktop),
        }
    }

    pub const fn is_safe(self) -> bool {
        self.source_id.is_some() && self.sensitive.is_none()
    }
}

pub fn normalize_excluded_applications(
    applications: &[String],
) -> Result<Vec<String>, &'static str> {
    if applications.len() > MAX_EXCLUDED_APPLICATIONS {
        return Err("REALTIME_EXCLUSIONS_INVALID");
    }
    let mut normalized = Vec::with_capacity(applications.len());
    for application in applications {
        let value = application.trim().to_ascii_lowercase();
        if value.is_empty()
            || value.len() > MAX_EXECUTABLE_BASENAME_BYTES
            || value
                != Path::new(&value)
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("")
            || value
                .chars()
                .any(|character| matches!(character, '*' | '?' | '/' | '\\'))
            || value.chars().any(char::is_control)
            || !value.ends_with(".exe")
            || normalized.contains(&value)
        {
            return Err("REALTIME_EXCLUSIONS_INVALID");
        }
        normalized.push(value);
    }
    Ok(normalized)
}

pub fn classify_window_metadata(
    title: &str,
    executable_basename: &str,
    owner_process_id: u32,
    desktop_process_id: u32,
    excluded_applications: &[String],
) -> Option<RealtimeSensitiveCategory> {
    let executable = executable_basename.trim().to_ascii_lowercase();
    let normalized_title = title.trim().to_ascii_lowercase();
    if owner_process_id == desktop_process_id {
        return Some(RealtimeSensitiveCategory::FairyOwned);
    }
    if excluded_applications
        .iter()
        .any(|excluded| excluded == &executable)
    {
        return Some(RealtimeSensitiveCategory::UserExcluded);
    }
    if [
        "1password.exe",
        "bitwarden.exe",
        "keepass.exe",
        "keepassxc.exe",
        "dashlane.exe",
        "lastpass.exe",
        "credentialui.exe",
    ]
    .contains(&executable.as_str())
    {
        return Some(RealtimeSensitiveCategory::CredentialApplication);
    }
    if [
        "password",
        "passkey",
        "credential",
        "private browsing",
        "incognito",
        "payment",
        "checkout",
        "banking",
        "bank account",
        "wallet",
    ]
    .iter()
    .any(|marker| normalized_title.contains(marker))
    {
        return Some(RealtimeSensitiveCategory::FinancialOrPrivate);
    }
    None
}

#[cfg(target_os = "windows")]
pub fn sample_foreground_window(excluded_applications: &[String]) -> RealtimeWindowDecision {
    use windows_sys::Win32::UI::WindowsAndMessaging::GetForegroundWindow;

    let handle = unsafe { GetForegroundWindow() };
    if handle.is_null() {
        return RealtimeWindowDecision::unavailable();
    }
    inspect_window(u64::from(handle as usize as u32), excluded_applications)
}

#[cfg(not(target_os = "windows"))]
pub fn sample_foreground_window(_excluded_applications: &[String]) -> RealtimeWindowDecision {
    RealtimeWindowDecision::unavailable()
}

#[cfg(target_os = "windows")]
pub fn inspect_window(source_id: u64, excluded_applications: &[String]) -> RealtimeWindowDecision {
    use xcap::Window;

    let Ok(source_id) = u32::try_from(source_id) else {
        return RealtimeWindowDecision {
            source_id: None,
            sensitive: Some(RealtimeSensitiveCategory::ProtectedContent),
        };
    };
    let Ok(windows) = Window::all() else {
        return RealtimeWindowDecision::unavailable();
    };
    let Some(window) = windows
        .into_iter()
        .find(|window| window.id().ok() == Some(source_id))
    else {
        return RealtimeWindowDecision {
            source_id: None,
            sensitive: Some(RealtimeSensitiveCategory::ProtectedContent),
        };
    };
    let Ok(owner_process_id) = window.pid() else {
        return RealtimeWindowDecision::unavailable();
    };
    let title = window.title().unwrap_or_default();
    let executable = window.app_name().unwrap_or_default();
    RealtimeWindowDecision {
        source_id: Some(u64::from(source_id)),
        sensitive: classify_window_metadata(
            &title,
            &executable,
            owner_process_id,
            std::process::id(),
            excluded_applications,
        ),
    }
}

#[cfg(not(target_os = "windows"))]
pub fn inspect_window(
    _source_id: u64,
    _excluded_applications: &[String],
) -> RealtimeWindowDecision {
    RealtimeWindowDecision::unavailable()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exclusions_are_normalized_basenames_only() {
        assert_eq!(
            normalize_excluded_applications(&[" OBS64.EXE ".to_owned(), "game.exe".to_owned()])
                .expect("valid exclusions"),
            vec!["obs64.exe", "game.exe"]
        );
        for invalid in [
            vec!["C:\\tools\\secret.exe".to_owned()],
            vec!["*.exe".to_owned()],
            vec!["same.exe".to_owned(), "SAME.EXE".to_owned()],
            vec!["not-an-executable".to_owned()],
        ] {
            assert!(normalize_excluded_applications(&invalid).is_err());
        }
    }

    #[test]
    fn classifier_returns_categories_without_raw_metadata() {
        assert_eq!(
            classify_window_metadata("Vault", "Bitwarden.exe", 20, 10, &[]),
            Some(RealtimeSensitiveCategory::CredentialApplication)
        );
        assert_eq!(
            classify_window_metadata("Private Browsing", "browser.exe", 20, 10, &[]),
            Some(RealtimeSensitiveCategory::FinancialOrPrivate)
        );
        assert_eq!(
            classify_window_metadata("Fairy", "fairy.exe", 10, 10, &[]),
            Some(RealtimeSensitiveCategory::FairyOwned)
        );
        assert_eq!(
            classify_window_metadata("Safe", "excluded.exe", 20, 10, &["excluded.exe".to_owned()]),
            Some(RealtimeSensitiveCategory::UserExcluded)
        );
        assert_eq!(
            classify_window_metadata("Editor", "code.exe", 20, 10, &[]),
            None
        );
    }
}
