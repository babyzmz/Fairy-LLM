use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::WorkerError;

const COPY_BUFFER_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, serde::Serialize)]
pub struct WorkspaceObject {
    pub content_hash: String,
    pub byte_length: u64,
    pub storage_path: PathBuf,
}

pub struct ObjectStore {
    root: PathBuf,
    staging: PathBuf,
}

impl ObjectStore {
    pub fn new(managed_root: &Path) -> Result<Self, WorkerError> {
        let root = managed_root.join("objects").join("sha256");
        let staging = managed_root.join(".transactions").join("objects");
        fs::create_dir_all(&root)?;
        fs::create_dir_all(&staging)?;
        Ok(Self { root, staging })
    }

    pub fn put_file(
        &self,
        source: &Path,
        expected_hash: Option<&str>,
        workspace_bytes: u64,
        max_file_bytes: u64,
        max_workspace_bytes: u64,
    ) -> Result<WorkspaceObject, WorkerError> {
        let source = source.canonicalize()?;
        let metadata = fs::symlink_metadata(&source)?;
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return Err(WorkerError::PathOutOfScope(source.display().to_string()));
        }
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let temporary = self.staging.join(format!("object-{nonce}.tmp"));
        let mut input = File::open(&source)?;
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        let mut digest = Sha256::new();
        let mut byte_length = 0_u64;
        let mut buffer = vec![0_u8; COPY_BUFFER_BYTES];
        let result = (|| {
            loop {
                let count = input.read(&mut buffer)?;
                if count == 0 {
                    break;
                }
                byte_length = byte_length.saturating_add(count as u64);
                if byte_length > max_file_bytes {
                    return Err(WorkerError::FileTooLarge(byte_length));
                }
                if workspace_bytes.saturating_add(byte_length) > max_workspace_bytes {
                    return Err(WorkerError::WorkspaceQuotaExceeded(
                        workspace_bytes.saturating_add(byte_length),
                    ));
                }
                digest.update(&buffer[..count]);
                output.write_all(&buffer[..count])?;
            }
            output.sync_all()?;
            let content_hash = format!("{:x}", digest.finalize());
            if expected_hash.is_some_and(|expected| expected != content_hash) {
                return Err(WorkerError::ObjectDigestMismatch);
            }
            let target = self.root.join(&content_hash[..2]).join(&content_hash);
            fs::create_dir_all(target.parent().expect("object has parent"))?;
            if target.exists() {
                if target.metadata()?.len() != byte_length {
                    return Err(WorkerError::ObjectDigestMismatch);
                }
                fs::remove_file(&temporary)?;
            } else {
                fs::rename(&temporary, &target)?;
            }
            Ok(WorkspaceObject {
                content_hash,
                byte_length,
                storage_path: target,
            })
        })();
        if result.is_err() {
            let _ignored = fs::remove_file(temporary);
        }
        result
    }
}
