"""Copy the databases' state to persistent storage. Run by the `backup` service.

The databases live on the container's local disk, which is wiped when the pod stops. This
writes a PostgreSQL dump and one Qdrant snapshot per collection to the volume. Each file is
written under a temporary name and renamed, so a crash mid-backup never damages the last
good copy.

Usage: backup.py <backup dir> <qdrant local snapshots dir>
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

QDRANT = "http://127.0.0.1:6333"


def backup_postgres(backups: Path) -> None:
    target = backups / "postgres.dump"
    partial = backups / "postgres.dump.partial"
    subprocess.run(  # noqa: S603
        ["pg_dump", "-Fc", "-h", "127.0.0.1", "-U", "docqa", "-d", "docqa", "-f", str(partial)],  # noqa: S607
        check=True,
    )
    os.replace(partial, target)


def backup_qdrant(backups: Path, local_snapshots: Path) -> None:
    target_dir = backups / "qdrant"
    target_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=QDRANT, timeout=600) as client:
        names = [c["name"] for c in client.get("/collections").json()["result"]["collections"]]
        for name in names:
            created = client.post(f"/collections/{name}/snapshots", params={"wait": "true"})
            created.raise_for_status()
            snapshot = created.json()["result"]["name"]
            try:
                partial = target_dir / f"{name}.snapshot.partial"
                shutil.copyfile(local_snapshots / name / snapshot, partial)
                os.replace(partial, target_dir / f"{name}.snapshot")
            finally:
                client.delete(f"/collections/{name}/snapshots/{snapshot}", params={"wait": "true"})


def main() -> int:
    backups, local_snapshots = Path(sys.argv[1]), Path(sys.argv[2])
    backups.mkdir(parents=True, exist_ok=True)
    failed = False
    for step in (lambda: backup_postgres(backups), lambda: backup_qdrant(backups, local_snapshots)):
        try:
            step()
        except Exception as exc:  # one store failing must not skip the other
            print(f"[backup] {type(exc).__name__}: {exc}", flush=True)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
