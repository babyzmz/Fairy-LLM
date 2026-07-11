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
}
