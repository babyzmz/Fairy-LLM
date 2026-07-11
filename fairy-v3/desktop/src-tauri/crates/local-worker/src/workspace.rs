use serde::{Deserialize, Serialize};
use std::ffi::OsStr;
use std::fs;
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex, MutexGuard};

use crate::WorkerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionWorkspace {
    pub project_id: String,
    pub version_id: String,
    pub root: PathBuf,
}

#[derive(Debug, Serialize, Deserialize)]
struct ChangesetJournal {
    schema_version: u32,
    entries: Vec<ChangesetJournalEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ChangesetJournalEntry {
    relative_path: String,
    existed: bool,
}

#[derive(Debug, Deserialize)]
pub(crate) struct FileMutationParams {
    pub(crate) relative_path: String,
    pub(crate) content: String,
}

#[derive(Debug, Clone)]
pub struct WorkspaceManager {
    managed_root: PathBuf,
    operation_lock: Arc<Mutex<()>>,
}

impl WorkspaceManager {
    pub fn new(managed_root: impl AsRef<Path>) -> Self {
        Self {
            managed_root: managed_root.as_ref().to_path_buf(),
            operation_lock: Arc::new(Mutex::new(())),
        }
    }

    pub(crate) fn managed_root(&self) -> &Path {
        &self.managed_root
    }

    fn lock(&self) -> Result<MutexGuard<'_, ()>, WorkerError> {
        self.operation_lock
            .lock()
            .map_err(|_error| WorkerError::LockPoisoned)
    }

    pub fn import_project(
        &self,
        source: impl AsRef<Path>,
        project_id: &str,
        version_id: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let source = source.as_ref().canonicalize()?;
        let project_root = self.project_root(project_id);
        let repository = project_root.join("repo.git");
        let seed = project_root.join("seed");
        let version_root = self.version_root(project_id, version_id);
        if repository.is_dir() && version_root.is_dir() {
            return Ok(VersionWorkspace {
                project_id: project_id.to_owned(),
                version_id: version_id.to_owned(),
                root: version_root.canonicalize()?,
            });
        }
        fs::create_dir_all(&seed)?;
        copy_tree(&source, &seed)?;
        self.initialize_seed(project_id, version_id, &seed, "Imported project")
    }

    pub fn create_empty(
        &self,
        project_id: &str,
        version_id: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let version_root = self.version_root(project_id, version_id);
        if version_root.is_dir() {
            return Ok(VersionWorkspace {
                project_id: project_id.to_owned(),
                version_id: version_id.to_owned(),
                root: version_root.canonicalize()?,
            });
        }
        let seed = self.project_root(project_id).join("seed");
        fs::create_dir_all(&seed)?;
        self.initialize_seed(project_id, version_id, &seed, "Created empty project")
    }

    fn initialize_seed(
        &self,
        project_id: &str,
        version_id: &str,
        seed: &Path,
        commit_message: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        let repository = self.repository_path(project_id);
        let version_root = self.version_root(project_id, version_id);
        run_git([OsStr::new("init"), seed.as_os_str()])?;
        run_git_in(seed, ["config", "user.name", "Fairy Core"])?;
        run_git_in(seed, ["config", "user.email", "fairy@localhost"])?;
        run_git_in(seed, ["config", "core.autocrlf", "false"])?;
        run_git_in(seed, ["add", "--all"])?;
        run_git_in(seed, ["commit", "--allow-empty", "-m", commit_message])?;

        run_git([
            OsStr::new("init"),
            OsStr::new("--bare"),
            repository.as_os_str(),
        ])?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("config"),
            OsStr::new("user.name"),
            OsStr::new("Fairy Core"),
        ])?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("config"),
            OsStr::new("user.email"),
            OsStr::new("fairy@localhost"),
        ])?;
        run_git_in(
            seed,
            [
                "remote",
                "add",
                "origin",
                repository.to_string_lossy().as_ref(),
            ],
        )?;
        let branch = branch_name(version_id);
        run_git_in(
            seed,
            ["push", "origin", &format!("HEAD:refs/heads/{branch}")],
        )?;
        fs::remove_dir_all(seed)?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("worktree"),
            OsStr::new("add"),
            version_root.as_os_str(),
            OsStr::new(&branch),
        ])?;
        Ok(VersionWorkspace {
            project_id: project_id.to_owned(),
            version_id: version_id.to_owned(),
            root: version_root.canonicalize()?,
        })
    }

    pub fn fork_version(
        &self,
        project_id: &str,
        parent_version_id: &str,
        version_id: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(parent_version_id)?;
        validate_identifier(version_id)?;
        let repository = self.repository_path(project_id);
        let version_root = self.version_root(project_id, version_id);
        if version_root.is_dir() {
            return Ok(VersionWorkspace {
                project_id: project_id.to_owned(),
                version_id: version_id.to_owned(),
                root: version_root.canonicalize()?,
            });
        }
        let branch = branch_name(version_id);
        let parent_branch = branch_name(parent_version_id);
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("worktree"),
            OsStr::new("add"),
            OsStr::new("-b"),
            OsStr::new(&branch),
            version_root.as_os_str(),
            OsStr::new(&parent_branch),
        ])?;
        Ok(VersionWorkspace {
            project_id: project_id.to_owned(),
            version_id: version_id.to_owned(),
            root: version_root.canonicalize()?,
        })
    }

    pub fn write_file(
        &self,
        project_id: &str,
        version_id: &str,
        relative_path: &str,
        content: &[u8],
    ) -> Result<PathBuf, WorkerError> {
        let _operation = self.lock()?;
        self.write_file_unlocked(project_id, version_id, relative_path, content)
    }

    pub(crate) fn resolve_existing_path(
        &self,
        project_id: &str,
        version_id: &str,
        relative_path: &str,
    ) -> Result<PathBuf, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let managed_root = self.managed_root.canonicalize()?;
        let version_root = self.version_root(project_id, version_id).canonicalize()?;
        if !version_root.starts_with(&managed_root) {
            return Err(WorkerError::PathOutOfScope(relative_path.to_owned()));
        }
        let target = validate_scoped_target(&version_root, relative_path)?;
        if !target.exists() {
            return Err(WorkerError::PathOutOfScope(relative_path.to_owned()));
        }
        let canonical = target.canonicalize()?;
        if !canonical.starts_with(&version_root) {
            return Err(WorkerError::PathOutOfScope(relative_path.to_owned()));
        }
        Ok(canonical)
    }

    fn write_file_unlocked(
        &self,
        project_id: &str,
        version_id: &str,
        relative_path: &str,
        content: &[u8],
    ) -> Result<PathBuf, WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let version_root = self.version_root(project_id, version_id).canonicalize()?;
        let target = validate_scoped_target(&version_root, relative_path)?;
        let parent = target
            .parent()
            .ok_or_else(|| WorkerError::PathOutOfScope(relative_path.to_owned()))?;
        fs::create_dir_all(parent)?;
        if validate_scoped_target(&version_root, relative_path)? != target {
            return Err(WorkerError::PathOutOfScope(relative_path.to_owned()));
        }
        let staged = self
            .managed_root
            .join(".transactions/single-writes")
            .join(project_id)
            .join(version_id)
            .join("current.tmp");
        write_durable(&staged, content)?;
        if let Err(error) = replace_file(&staged, &target) {
            let _cleanup = fs::remove_file(&staged);
            return Err(error);
        }
        Ok(target)
    }

    pub(crate) fn apply_changeset(
        &self,
        project_id: &str,
        version_id: &str,
        mutations: &[FileMutationParams],
    ) -> Result<Vec<PathBuf>, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let version_root = self.version_root(project_id, version_id).canonicalize()?;
        let transaction_root = self
            .managed_root
            .join(".transactions/changesets")
            .join(project_id)
            .join(version_id)
            .join("current");
        recover_changeset(&transaction_root, &version_root)?;
        let targets = mutations
            .iter()
            .map(|mutation| validate_scoped_target(&version_root, &mutation.relative_path))
            .collect::<Result<Vec<_>, _>>()?;
        let journal = prepare_changeset(&transaction_root, &version_root, mutations, &targets)?;

        let apply_result = (|| -> Result<(), WorkerError> {
            for (index, (mutation, target)) in mutations.iter().zip(&targets).enumerate() {
                let parent = target
                    .parent()
                    .ok_or_else(|| WorkerError::PathOutOfScope(mutation.relative_path.clone()))?;
                fs::create_dir_all(parent)?;
                if validate_scoped_target(&version_root, &mutation.relative_path)? != *target {
                    return Err(WorkerError::PathOutOfScope(mutation.relative_path.clone()));
                }
                replace_file(
                    &transaction_root.join("staged").join(format!("{index}.bin")),
                    target,
                )?;
            }
            write_durable(&transaction_root.join("applied"), b"applied\n")
        })();
        if let Err(write_error) = apply_result {
            if let Err(rollback_error) =
                rollback_changeset(&transaction_root, &version_root, &journal)
            {
                return Err(WorkerError::ChangesetRollback {
                    write_error: write_error.to_string(),
                    rollback_error: rollback_error.to_string(),
                });
            }
            let _cleanup = fs::remove_dir_all(&transaction_root);
            return Err(write_error);
        }
        let _cleanup = fs::remove_dir_all(&transaction_root);
        Ok(targets)
    }

    pub fn checkpoint(
        &self,
        project_id: &str,
        version_id: &str,
        message: &str,
    ) -> Result<String, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let root = self.version_root(project_id, version_id).canonicalize()?;
        if run_git_in(&root, ["status", "--porcelain"])?
            .trim()
            .is_empty()
        {
            return Ok(run_git_in(&root, ["rev-parse", "HEAD"])?.trim().to_owned());
        }
        run_git_in(&root, ["add", "--all"])?;
        run_git_in(&root, ["commit", "-m", message])?;
        Ok(run_git_in(&root, ["rev-parse", "HEAD"])?.trim().to_owned())
    }

    pub fn create_scratch(
        &self,
        conversation_id: &str,
        task_id: &str,
    ) -> Result<PathBuf, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(conversation_id)?;
        validate_identifier(task_id)?;
        let root = self
            .managed_root
            .join("scratch")
            .join(conversation_id)
            .join(task_id);
        fs::create_dir_all(&root)?;
        Ok(root.canonicalize()?)
    }

    pub fn diff(&self, project_id: &str, version_id: &str) -> Result<String, WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let root = self.version_root(project_id, version_id).canonicalize()?;
        let status = run_git_in(&root, ["status", "--short"])?;
        let patch = run_git_in(&root, ["diff", "--no-ext-diff", "--binary", "HEAD"])?;
        Ok(format!("{status}{patch}"))
    }

    pub fn discard_version(&self, project_id: &str, version_id: &str) -> Result<(), WorkerError> {
        let _operation = self.lock()?;
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let repository = self.repository_path(project_id);
        let version_root = self.version_root(project_id, version_id);
        if !version_root.exists() {
            return Ok(());
        }
        let branch = branch_name(version_id);
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("worktree"),
            OsStr::new("remove"),
            OsStr::new("--force"),
            version_root.as_os_str(),
        ])?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("branch"),
            OsStr::new("-D"),
            OsStr::new(&branch),
        ])?;
        Ok(())
    }

    fn project_root(&self, project_id: &str) -> PathBuf {
        self.managed_root.join("projects").join(project_id)
    }

    fn repository_path(&self, project_id: &str) -> PathBuf {
        self.project_root(project_id).join("repo.git")
    }

    fn version_root(&self, project_id: &str, version_id: &str) -> PathBuf {
        self.project_root(project_id)
            .join("versions")
            .join(version_id)
    }
}

pub(crate) fn validate_identifier(value: &str) -> Result<(), WorkerError> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(WorkerError::InvalidIdentifier(value.to_owned()));
    }
    Ok(())
}

fn validate_relative_path(value: &str) -> Result<PathBuf, WorkerError> {
    if value.is_empty() || value.contains('\0') || value.contains(':') {
        return Err(WorkerError::PathOutOfScope(value.to_owned()));
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_) | Component::CurDir))
    {
        return Err(WorkerError::PathOutOfScope(value.to_owned()));
    }
    Ok(path.to_path_buf())
}

fn validate_scoped_target(
    version_root: &Path,
    relative_path: &str,
) -> Result<PathBuf, WorkerError> {
    let relative = validate_relative_path(relative_path)?;
    let target = version_root.join(relative);
    let mut existing_parent = target
        .parent()
        .ok_or_else(|| WorkerError::PathOutOfScope(relative_path.to_owned()))?;
    while !existing_parent.exists() {
        existing_parent = existing_parent
            .parent()
            .ok_or_else(|| WorkerError::PathOutOfScope(relative_path.to_owned()))?;
    }
    if !existing_parent.canonicalize()?.starts_with(version_root) {
        return Err(WorkerError::PathOutOfScope(relative_path.to_owned()));
    }
    if target.exists() && !target.canonicalize()?.starts_with(version_root) {
        return Err(WorkerError::PathOutOfScope(relative_path.to_owned()));
    }
    Ok(target)
}

fn prepare_changeset(
    transaction_root: &Path,
    version_root: &Path,
    mutations: &[FileMutationParams],
    targets: &[PathBuf],
) -> Result<ChangesetJournal, WorkerError> {
    if transaction_root.exists() {
        return Err(WorkerError::ChangesetJournal(
            transaction_root.display().to_string(),
        ));
    }
    fs::create_dir_all(transaction_root.join("backups"))?;
    fs::create_dir_all(transaction_root.join("staged"))?;
    let result = (|| -> Result<ChangesetJournal, WorkerError> {
        let mut entries: Vec<ChangesetJournalEntry> = Vec::new();
        let mut unique_targets: Vec<PathBuf> = Vec::new();
        for (mutation, target) in mutations.iter().zip(targets) {
            if unique_targets.iter().any(|existing| existing == target) {
                continue;
            }
            let checked = validate_scoped_target(version_root, &mutation.relative_path)?;
            let existed = checked.exists();
            if existed {
                write_durable(
                    &transaction_root
                        .join("backups")
                        .join(format!("{}.bin", entries.len())),
                    &fs::read(&checked)?,
                )?;
            }
            unique_targets.push(target.clone());
            entries.push(ChangesetJournalEntry {
                relative_path: mutation.relative_path.clone(),
                existed,
            });
        }
        for (index, mutation) in mutations.iter().enumerate() {
            write_durable(
                &transaction_root.join("staged").join(format!("{index}.bin")),
                mutation.content.as_bytes(),
            )?;
        }
        let journal = ChangesetJournal {
            schema_version: 1,
            entries,
        };
        let manifest = serde_json::to_vec(&journal)
            .map_err(|error| WorkerError::ChangesetJournal(error.to_string()))?;
        write_durable(&transaction_root.join("manifest.json"), &manifest)?;
        Ok(journal)
    })();
    if result.is_err() {
        let _cleanup = fs::remove_dir_all(transaction_root);
    }
    result
}

fn recover_changeset(transaction_root: &Path, version_root: &Path) -> Result<(), WorkerError> {
    if !transaction_root.exists() {
        return Ok(());
    }
    let manifest_path = transaction_root.join("manifest.json");
    if !manifest_path.is_file() {
        fs::remove_dir_all(transaction_root)?;
        return Ok(());
    }
    if transaction_root.join("applied").is_file() {
        fs::remove_dir_all(transaction_root)?;
        return Ok(());
    }
    let journal: ChangesetJournal = serde_json::from_slice(&fs::read(&manifest_path)?)
        .map_err(|error| WorkerError::ChangesetJournal(error.to_string()))?;
    if journal.schema_version != 1 {
        return Err(WorkerError::ChangesetJournal(format!(
            "unsupported schema version {}",
            journal.schema_version
        )));
    }
    rollback_changeset(transaction_root, version_root, &journal)?;
    fs::remove_dir_all(transaction_root)?;
    Ok(())
}

fn rollback_changeset(
    transaction_root: &Path,
    version_root: &Path,
    journal: &ChangesetJournal,
) -> Result<(), WorkerError> {
    for (index, entry) in journal.entries.iter().enumerate() {
        let target = validate_scoped_target(version_root, &entry.relative_path)?;
        if entry.existed {
            let backup = transaction_root
                .join("backups")
                .join(format!("{index}.bin"));
            if !backup.is_file() {
                return Err(WorkerError::ChangesetJournal(format!(
                    "missing backup: {}",
                    backup.display()
                )));
            }
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            if validate_scoped_target(version_root, &entry.relative_path)? != target {
                return Err(WorkerError::PathOutOfScope(entry.relative_path.clone()));
            }
            let staged = transaction_root
                .join("rollback")
                .join(format!("{index}.bin"));
            write_durable(&staged, &fs::read(backup)?)?;
            replace_file(&staged, &target)?;
        } else if target.exists() {
            if target.is_dir() {
                return Err(WorkerError::PathOutOfScope(entry.relative_path.clone()));
            }
            fs::remove_file(target)?;
        }
    }
    Ok(())
}

fn write_durable(path: &Path, content: &[u8]) -> Result<(), WorkerError> {
    let parent = path.parent().ok_or_else(|| {
        WorkerError::ChangesetJournal(format!("path has no parent: {}", path.display()))
    })?;
    fs::create_dir_all(parent)?;
    let name = path.file_name().and_then(OsStr::to_str).ok_or_else(|| {
        WorkerError::ChangesetJournal(format!("path has no file name: {}", path.display()))
    })?;
    let temporary = path.with_file_name(format!(".{name}.tmp"));
    let result = (|| -> Result<(), WorkerError> {
        let mut file = fs::File::create(&temporary)?;
        file.write_all(content)?;
        file.sync_all()?;
        drop(file);
        replace_file(&temporary, path)
    })();
    if result.is_err() {
        let _cleanup = fs::remove_file(&temporary);
    }
    result
}

fn replace_file(source: &Path, target: &Path) -> Result<(), WorkerError> {
    if target.is_file() {
        fs::set_permissions(source, fs::metadata(target)?.permissions())?;
    }
    fs::rename(source, target)?;
    Ok(())
}

fn copy_tree(source: &Path, destination: &Path) -> Result<(), WorkerError> {
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let name = entry.file_name();
        if matches!(
            name.to_string_lossy().as_ref(),
            ".git" | ".venv" | "node_modules" | "target"
        ) {
            continue;
        }
        let file_type = entry.file_type()?;
        if file_type.is_symlink() {
            return Err(WorkerError::PathOutOfScope(
                entry.path().display().to_string(),
            ));
        }
        let target = destination.join(&name);
        if file_type.is_dir() {
            fs::create_dir_all(&target)?;
            copy_tree(&entry.path(), &target)?;
        } else if file_type.is_file() {
            fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

fn branch_name(version_id: &str) -> String {
    format!("version/{version_id}")
}

fn run_git<I, S>(arguments: I) -> Result<String, WorkerError>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let output = Command::new("git").args(arguments).output()?;
    if !output.status.success() {
        return Err(WorkerError::Git(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn run_git_in<const N: usize>(
    directory: &Path,
    arguments: [&str; N],
) -> Result<String, WorkerError> {
    let output = Command::new("git")
        .arg("-C")
        .arg(directory)
        .args(arguments)
        .output()?;
    if !output.status.success() {
        return Err(WorkerError::Git(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}
