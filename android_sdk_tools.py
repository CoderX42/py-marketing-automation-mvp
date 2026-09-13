"""Install the Windows SDK command-line tools without PowerShell Expand-Archive.

Called after Python is available. Only the standard library is required.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import uuid
import zipfile


REQUIRED_FILES = ("bin/sdkmanager.bat", "lib/sdkmanager-classpath.jar", "source.properties")


def filesystem_path(value: str | Path) -> Path:
    """Use extended Windows paths without changing system long-path settings."""
    path = os.path.abspath(value)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        path = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return Path(path)


def complete_tools(path: Path) -> bool:
    return all((path / name).is_file() and (path / name).stat().st_size > 0 for name in REQUIRED_FILES)


def install_archive(archive: str | Path, destination: str | Path, sha1: str) -> Path:
    archive, destination = filesystem_path(archive), filesystem_path(destination)
    digest = hashlib.sha1()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != sha1.strip().lower():
        raise ValueError("Android ZIP checksum does not match the official repository.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Same-volume staging keeps replacement local; short names also reduce
    # the length of SDK library paths passed to other Windows tools.
    with tempfile.TemporaryDirectory(prefix=".sdk-", dir=str(destination.parent)) as temp:
        staging = Path(temp)
        with zipfile.ZipFile(archive) as bundle:
            entries = []
            for member in bundle.infolist():
                relative = PurePosixPath(member.filename.replace("\\", "/"))
                if (relative.is_absolute() or ".." in relative.parts or
                        any(":" in part for part in relative.parts) or
                        stat.S_ISLNK(member.external_attr >> 16)):
                    raise ValueError(f"Invalid path in Android ZIP: {member.filename}")
                entries.append((member, staging.joinpath(*relative.parts)))
            for member, target in entries:
                if member.is_dir() or member.filename.endswith("\\"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                # Google's ZIP need not contain explicit directory entries.
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                # Reading to EOF also checks each file's CRC.

        candidates = {manager.parent.parent for manager in staging.rglob("sdkmanager.bat")
                      if complete_tools(manager.parent.parent)}
        if len(candidates) != 1:
            raise ValueError("Android ZIP lacks a unique complete sdkmanager/bin/lib layout.")
        payload = candidates.pop()
        backup = destination.with_name(destination.name + ".previous-" + uuid.uuid4().hex[:8])
        had_previous = destination.exists()
        if had_previous:
            destination.rename(backup)
        try:
            payload.rename(destination)
        except BaseException:
            if had_previous:
                backup.rename(destination)
            raise
        if had_previous:
            # The new installation is already complete. A locked obsolete
            # file must not turn a successful installation into a failure.
            shutil.rmtree(backup, ignore_errors=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("destination")
    parser.add_argument("--sha1", required=True)
    args = parser.parse_args()
    install_archive(args.archive, args.destination, args.sha1)
    print("Android command-line tools extracted and verified.")


if __name__ == "__main__":
    main()
