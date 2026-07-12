use std::fs;
use std::path::{Path, PathBuf};

use thiserror::Error;

const CREDENTIAL_FILE: &str = "credentials/openrouter.dpapi";

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
        let protected = protect(normalized.as_bytes())?;
        let parent = self.path.parent().ok_or_else(|| {
            CredentialError::Io(std::io::Error::other("credential path has no parent"))
        })?;
        fs::create_dir_all(parent)?;
        let temporary = self.path.with_extension("tmp");
        fs::write(&temporary, protected)?;
        fs::rename(temporary, &self.path)?;
        Ok(())
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
}
