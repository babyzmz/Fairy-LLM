use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use thiserror::Error;

const CREDENTIAL_FILE: &str = "credentials/openrouter.dpapi";
static TEMPORARY_FILE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Error)]
pub enum CredentialError {
    #[error("provider credential is empty")]
    Empty,
    #[error("provider credential storage failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("Windows credential protection failed: {0}")]
    Windows(u32),
    #[error("provider credential is not valid UTF-8")]
    Encoding,
    #[error("secure provider credential storage requires Windows")]
    Unsupported,
}

pub struct ProviderCredentialStore {
    path: PathBuf,
}

pub struct CredentialReplacement {
    path: PathBuf,
    previous: Option<Vec<u8>>,
    finished: bool,
}

impl ProviderCredentialStore {
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        Self {
            path: data_dir.as_ref().join(CREDENTIAL_FILE),
        }
    }

    pub fn configured(&self) -> bool {
        self.path.is_file()
    }

    pub fn save_openrouter(&self, secret: &str) -> Result<(), CredentialError> {
        let normalized = secret.trim();
        if normalized.is_empty() {
            return Err(CredentialError::Empty);
        }
        write_protected(&self.path, &protect(normalized.as_bytes())?)
    }

    pub fn begin_openrouter_replacement(
        &self,
        secret: &str,
    ) -> Result<CredentialReplacement, CredentialError> {
        let previous = match fs::read(&self.path) {
            Ok(value) => Some(value),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        self.save_openrouter(secret)?;
        Ok(CredentialReplacement {
            path: self.path.clone(),
            previous,
            finished: false,
        })
    }

    pub fn load_openrouter(&self) -> Result<Option<String>, CredentialError> {
        if !self.path.is_file() {
            return Ok(None);
        }
        let protected = fs::read(&self.path)?;
        let clear = unprotect(&protected)?;
        String::from_utf8(clear)
            .map(Some)
            .map_err(|_| CredentialError::Encoding)
    }

    pub fn delete_openrouter(&self) -> Result<(), CredentialError> {
        match fs::remove_file(&self.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error.into()),
        }
    }
}

impl CredentialReplacement {
    pub fn commit(mut self) {
        self.finished = true;
    }

    pub fn rollback(mut self) -> Result<(), CredentialError> {
        restore_snapshot(&self.path, self.previous.as_deref())?;
        self.finished = true;
        Ok(())
    }
}

impl Drop for CredentialReplacement {
    fn drop(&mut self) {
        if !self.finished {
            let _ = restore_snapshot(&self.path, self.previous.as_deref());
        }
    }
}

fn restore_snapshot(path: &Path, previous: Option<&[u8]>) -> Result<(), CredentialError> {
    match previous {
        Some(protected) => write_protected(path, protected),
        None => match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error.into()),
        },
    }
}

fn write_protected(path: &Path, protected: &[u8]) -> Result<(), CredentialError> {
    let parent = path.parent().ok_or_else(|| {
        CredentialError::Io(std::io::Error::other("credential path has no parent"))
    })?;
    fs::create_dir_all(parent)?;
    let sequence = TEMPORARY_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = path.with_extension(format!("tmp-{}-{sequence}", std::process::id()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        file.write_all(protected)?;
        file.sync_all()?;
        replace_file(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result.map_err(CredentialError::from)
}

#[cfg(windows)]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source = source
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let succeeded = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if succeeded == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    fs::rename(source, destination)
}

#[cfg(windows)]
fn protect(clear: &[u8]) -> Result<Vec<u8>, CredentialError> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let input = CRYPT_INTEGER_BLOB {
        cbData: clear.len().try_into().map_err(|_| CredentialError::Empty)?,
        pbData: clear.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    let succeeded = unsafe {
        CryptProtectData(
            &input,
            ptr::null(),
            ptr::null(),
            ptr::null(),
            ptr::null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if succeeded == 0 {
        return Err(CredentialError::Windows(
            std::io::Error::last_os_error().raw_os_error().unwrap_or(0) as u32,
        ));
    }
    let protected =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
    unsafe { LocalFree(output.pbData.cast()) };
    Ok(protected)
}

#[cfg(windows)]
fn unprotect(protected: &[u8]) -> Result<Vec<u8>, CredentialError> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let input = CRYPT_INTEGER_BLOB {
        cbData: protected
            .len()
            .try_into()
            .map_err(|_| CredentialError::Empty)?,
        pbData: protected.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    let succeeded = unsafe {
        CryptUnprotectData(
            &input,
            ptr::null_mut(),
            ptr::null(),
            ptr::null(),
            ptr::null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if succeeded == 0 {
        return Err(CredentialError::Windows(
            std::io::Error::last_os_error().raw_os_error().unwrap_or(0) as u32,
        ));
    }
    let clear =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
    unsafe { LocalFree(output.pbData.cast()) };
    Ok(clear)
}

#[cfg(not(windows))]
fn protect(_clear: &[u8]) -> Result<Vec<u8>, CredentialError> {
    Err(CredentialError::Unsupported)
}

#[cfg(not(windows))]
fn unprotect(_protected: &[u8]) -> Result<Vec<u8>, CredentialError> {
    Err(CredentialError::Unsupported)
}

#[cfg(all(test, windows))]
mod tests {
    use super::ProviderCredentialStore;

    #[test]
    fn openrouter_credential_is_encrypted_replaceable_and_deletable() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = ProviderCredentialStore::new(directory.path());

        assert!(!store.configured());
        assert_eq!(store.load_openrouter().expect("missing credential"), None);

        store
            .save_openrouter("sk-or-v1-original-secret")
            .expect("save credential");
        assert!(store.configured());
        assert_eq!(
            store.load_openrouter().expect("load credential").as_deref(),
            Some("sk-or-v1-original-secret")
        );
        let stored = std::fs::read(directory.path().join("credentials/openrouter.dpapi"))
            .expect("encrypted file");
        assert!(!stored
            .windows(b"sk-or-v1-original-secret".len())
            .any(|window| window == b"sk-or-v1-original-secret"));

        store
            .save_openrouter("sk-or-v1-replaced-secret")
            .expect("replace credential");
        assert_eq!(
            store
                .load_openrouter()
                .expect("load replacement")
                .as_deref(),
            Some("sk-or-v1-replaced-secret")
        );

        store.delete_openrouter().expect("delete credential");
        assert!(!store.configured());
        assert_eq!(store.load_openrouter().expect("deleted credential"), None);
    }

    #[test]
    fn candidate_replacement_rolls_back_opaque_previous_credential() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = ProviderCredentialStore::new(directory.path());
        store
            .save_openrouter("sk-or-v1-original-secret")
            .expect("save original");

        let replacement = store
            .begin_openrouter_replacement("sk-or-v1-invalid-candidate")
            .expect("stage candidate");
        assert_eq!(
            store.load_openrouter().expect("load candidate").as_deref(),
            Some("sk-or-v1-invalid-candidate")
        );
        replacement.rollback().expect("restore original");

        assert_eq!(
            store.load_openrouter().expect("load restored").as_deref(),
            Some("sk-or-v1-original-secret")
        );
    }

    #[test]
    fn uncommitted_first_candidate_leaves_no_credential() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = ProviderCredentialStore::new(directory.path());

        let replacement = store
            .begin_openrouter_replacement("sk-or-v1-first-candidate")
            .expect("stage first candidate");
        drop(replacement);

        assert_eq!(
            store.load_openrouter().expect("rolled back candidate"),
            None
        );
    }

    #[test]
    fn committed_candidate_survives_replacement_guard() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = ProviderCredentialStore::new(directory.path());

        let replacement = store
            .begin_openrouter_replacement("sk-or-v1-valid-candidate")
            .expect("stage candidate");
        replacement.commit();

        assert_eq!(
            store.load_openrouter().expect("load committed").as_deref(),
            Some("sk-or-v1-valid-candidate")
        );
    }
}
