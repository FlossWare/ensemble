import os
import shutil
import sqlite3
import tarfile
import subprocess
from pathlib import Path

import pytest


def _write_partition_database(path: Path, partition_id: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ensemble_partition_metadata (partition_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO ensemble_partition_metadata(partition_id) VALUES (?)",
            (partition_id,),
        )
        connection.execute("CREATE TABLE payload (value TEXT NOT NULL)")
        connection.execute("INSERT INTO payload(value) VALUES (?)", (value,))


def _run_as_root(command: list[str], env: dict[str, str]) -> None:
    if os.geteuid() == 0:
        invocation = ["env", *(f"{key}={value}" for key, value in env.items()), *command]
    elif shutil.which("sudo"):
        invocation = ["sudo", "env", *(f"{key}={value}" for key, value in env.items()), *command]
    else:
        pytest.skip("backup/restore integration test requires root or passwordless sudo")
    completed = subprocess.run(invocation, check=False, capture_output=True, text=True)
    if completed.returncode:
        pytest.fail(
            f"command failed ({completed.returncode}): {' '.join(command)}\\n"
            f"stdout:\\n{completed.stdout}\\nstderr:\\n{completed.stderr}"
        )


def test_partition_backup_and_restore_round_trip(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    root = tmp_path / "ensemble-data"
    partition_file = root / "partitions" / ("a" * 64 + ".db")
    _write_partition_database(root / "ensemble.db", "default", "original-default")
    _write_partition_database(partition_file, "workspace-a", "original-partition")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text("#!/bin/sh\nif [ \"$1\" = \"is-active\" ]; then exit 3; fi\nexit 0\n")
    fake_systemctl.chmod(0o755)

    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "ENSEMBLE_PERSISTENCE_ROOT": str(root),
        "ENSEMBLE_SERVICE_NAME": "ensemble-test",
    }
    archive = tmp_path / "backups" / "ensemble-data.tar.gz"
    _run_as_root(
        ["bash", str(repository / "scripts/backup-persistence.sh"), str(archive)],
        env,
    )
    assert archive.is_file()

    # Replace the live root with unrelated data. Restore must preserve it as rollback.
    _write_partition_database(root / "ensemble.db", "default", "replacement-default")
    (root / "partitions").rename(root / "partitions-before-restore")
    _run_as_root(
        ["bash", str(repository / "scripts/restore-persistence.sh"), str(archive)],
        env,
    )

    with sqlite3.connect(root / "ensemble.db") as connection:
        assert connection.execute("SELECT value FROM payload").fetchone()[0] == "original-default"
    with sqlite3.connect(partition_file) as connection:
        assert connection.execute("SELECT value FROM payload").fetchone()[0] == "original-partition"
    rollback = list(tmp_path.glob("ensemble-data.pre-restore-*"))
    assert len(rollback) == 1
    assert (rollback[0] / "ensemble.db").is_file()


def test_restore_rejects_symlink_traversal_before_extraction(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    root = tmp_path / "ensemble-data"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("existing persistence must remain untouched")
    outside = tmp_path / "outside"
    archive = tmp_path / "malicious.tar.gz"

    with tarfile.open(archive, "w:gz") as bundle:
        link = tarfile.TarInfo("ensemble-data/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        bundle.addfile(link)

        payload = b"this must never be extracted"
        child = tarfile.TarInfo("ensemble-data/escape/created.txt")
        child.size = len(payload)
        import io
        bundle.addfile(child, io.BytesIO(payload))

    env = {
        "ENSEMBLE_PERSISTENCE_ROOT": str(root),
        "ENSEMBLE_SERVICE_NAME": "ensemble-test",
    }
    command = ["bash", str(repository / "scripts/restore-persistence.sh"), str(archive), str(root)]
    if os.geteuid() == 0:
        invocation = ["env", *(f"{key}={value}" for key, value in env.items()), *command]
    elif shutil.which("sudo"):
        invocation = ["sudo", "env", *(f"{key}={value}" for key, value in env.items()), *command]
    else:
        pytest.skip("restore security test requires root or passwordless sudo")
    completed = subprocess.run(invocation, check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "unsupported link or special entry" in completed.stderr
    assert sentinel.read_text() == "existing persistence must remain untouched"
    assert not outside.exists()
