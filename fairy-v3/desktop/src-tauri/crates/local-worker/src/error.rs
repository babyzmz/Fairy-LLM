use thiserror::Error;

#[derive(Debug, Error)]
pub enum WorkerError {
    #[error("I/O failure: {0}")]
    Io(#[from] std::io::Error),
    #[error("git command failed: {0}")]
    Git(String),
    #[error("path is outside the scoped version: {0}")]
    PathOutOfScope(String),
    #[error("identifier contains unsupported characters: {0}")]
    InvalidIdentifier(String),
    #[error("changeset rollback failed after {write_error}: {rollback_error}")]
    ChangesetRollback {
        write_error: String,
        rollback_error: String,
    },
    #[error("invalid changeset journal: {0}")]
    ChangesetJournal(String),
    #[error("workspace operation lock is poisoned")]
    LockPoisoned,
    #[error("Preview identity is already bound to another Scope: {0}")]
    PreviewScopeMismatch(String),
    #[error("Preview executor state is unavailable: {0}")]
    PreviewUnavailable(String),
    #[error("Preview server failed: {0}")]
    PreviewServer(String),
    #[error("Workspace file exceeds the per-file limit: {0} bytes")]
    FileTooLarge(u64),
    #[error("Workspace byte quota exceeded: {0} bytes")]
    WorkspaceQuotaExceeded(u64),
    #[error("Workspace object digest does not match")]
    ObjectDigestMismatch,
    #[error("Workspace AssetMutation target conflict: {0}")]
    ObjectTargetConflict(String),
    #[error("file read stream failed: {0}")]
    ReadStream(String),
}
