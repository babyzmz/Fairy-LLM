use std::fs;

use fairy_local_worker::{WorkerError, WorkspaceManager};
use tempfile::tempdir;

#[test]
fn import_and_fork_keep_source_and_parent_unchanged() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    let managed = temp.path().join("managed");
    fs::create_dir_all(source.join("src")).expect("source dir");
    fs::write(source.join("src/app.txt"), "base").expect("source file");
    let manager = WorkspaceManager::new(&managed);

    let base = manager
        .import_project(&source, "project-1", "version-base")
        .expect("import project");
    let draft = manager
        .fork_version("project-1", "version-base", "version-draft")
        .expect("fork version");
    manager
        .write_file("project-1", "version-draft", "src/app.txt", b"draft")
        .expect("write draft");

    assert_eq!(
        fs::read_to_string(source.join("src/app.txt")).unwrap(),
        "base"
    );
    assert_eq!(
        fs::read_to_string(base.root.join("src/app.txt")).unwrap(),
        "base"
    );
    assert_eq!(
        fs::read_to_string(draft.root.join("src/app.txt")).unwrap(),
        "draft"
    );
    assert!(managed.join("projects/project-1/repo.git").is_dir());
}

#[test]
fn write_file_rejects_path_escape() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(temp.path().join("managed"));
    manager
        .import_project(&source, "project-1", "version-base")
        .expect("import project");

    let error = manager
        .write_file("project-1", "version-base", "../escaped.txt", b"blocked")
        .expect_err("escape must fail");

    assert!(matches!(error, WorkerError::PathOutOfScope(_)));
    assert!(!temp
        .path()
        .join("managed/projects/project-1/versions/escaped.txt")
        .exists());
}

#[test]
fn checkpoint_commits_changes_to_the_version_branch() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(temp.path().join("managed"));
    manager
        .import_project(&source, "project-1", "version-base")
        .expect("import project");
    manager
        .write_file("project-1", "version-base", "README.md", b"updated")
        .expect("write file");

    let commit = manager
        .checkpoint("project-1", "version-base", "Task completed")
        .expect("checkpoint");

    assert_eq!(commit.len(), 40);
    assert!(commit.bytes().all(|byte| byte.is_ascii_hexdigit()));

    let retried = manager
        .checkpoint("project-1", "version-base", "Task completed")
        .expect("idempotent checkpoint");
    assert_eq!(retried, commit);
}

#[test]
fn importing_and_forking_the_same_version_is_idempotent() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(temp.path().join("managed"));

    let imported = manager
        .import_project(&source, "project-1", "version-base")
        .expect("first import");
    let import_retry = manager
        .import_project(&source, "project-1", "version-base")
        .expect("retry import");
    let draft = manager
        .fork_version("project-1", "version-base", "version-draft")
        .expect("first fork");
    let fork_retry = manager
        .fork_version("project-1", "version-base", "version-draft")
        .expect("retry fork");

    assert_eq!(import_retry.root, imported.root);
    assert_eq!(fork_retry.root, draft.root);
}

#[test]
fn diff_reports_changes_and_discard_removes_only_the_draft() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(temp.path().join("managed"));
    let base = manager
        .import_project(&source, "project-1", "version-base")
        .expect("import project");
    let draft = manager
        .fork_version("project-1", "version-base", "version-draft")
        .expect("fork version");
    manager
        .write_file("project-1", "version-draft", "README.md", b"draft")
        .expect("write draft");

    let diff = manager
        .diff("project-1", "version-draft")
        .expect("version diff");
    manager
        .discard_version("project-1", "version-draft")
        .expect("discard draft");
    manager
        .discard_version("project-1", "version-draft")
        .expect("discard retry");

    assert!(diff.contains("README.md"));
    assert!(base.root.is_dir());
    assert!(!draft.root.exists());
}
