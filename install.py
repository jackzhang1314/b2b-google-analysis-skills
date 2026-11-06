"""Install selected analysis skills into one project, with verified files and backups."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import uuid4

ADS = (
    "google-ads-analysis",
    "google-ads-diagnose",
    "google-ads-search-terms",
    "google-ads-measurement",
)
PROFILES = {
    "standard": ("b2b-ga4-gsc", *ADS),
    "ga4-gsc": ("b2b-ga4-gsc",),
    "ads": ADS,
    "b2b-ads": ("b2b-google-ads",),
    "all": ("b2b-ga4-gsc", *ADS, "b2b-google-ads"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Refusing a symbolic link: {path}")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            result[path.relative_to(directory).as_posix()] = digest(path)
    return result


def verify_package(package: Path) -> dict[str, str]:
    manifest_path = package / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Manifest must be a regular file")
    raw: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Invalid package manifest")
    manifest = cast(dict[str, object], raw)
    values = manifest.get("files")
    if not isinstance(values, dict) or not values:
        raise ValueError("Manifest has no file inventory")
    entries = cast(dict[object, object], values)
    expected: dict[str, str] = {}
    for relative, sha in entries.items():
        if not isinstance(relative, str) or not isinstance(sha, str):
            raise ValueError("Invalid manifest entry")
        rel = PurePosixPath(relative)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or "\\" in relative
            or ":" in relative
            or rel.as_posix() != relative
            or relative == "manifest.json"
            or not re.fullmatch(r"[0-9a-f]{64}", sha)
        ):
            raise ValueError(f"Invalid manifest path or digest: {relative}")
        path = package / relative
        if any((package / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
            raise ValueError(f"Package contains a symbolic link: {relative}")
        if not path.is_file() or digest(path) != sha:
            raise ValueError(f"Package integrity check failed: {relative}")
        expected[relative] = sha
    skill_root = package / "skills"
    if skill_root.is_symlink():
        raise ValueError("Skill source directory must not be a symbolic link")
    actual = {f"skills/{key}": value for key, value in inventory(skill_root).items()}
    if actual != {key: value for key, value in expected.items() if key.startswith("skills/")}:
        raise ValueError("Skill files do not match the manifest")
    for name in PROFILES["all"]:
        if f"skills/{name}/SKILL.md" not in expected:
            raise ValueError(f"Incomplete package: {name}")
    return expected


def install(
    package: Path, project: Path, profile: str = "standard", *,
    upgrade: bool = False, check: bool = False,
) -> dict[str, object]:
    package, project = package.resolve(), project.resolve()
    verify_package(package)
    if profile not in PROFILES:
        raise ValueError(f"Unknown installation profile: {profile}")
    if project in {Path.home().resolve(), Path(project.anchor)} or project.is_relative_to(package):
        raise ValueError("Choose a separate customer project, not your home or the release directory")
    if project.exists() and not project.is_dir():
        raise ValueError("Project path is not a directory")
    destination = project / ".agents" / "skills"
    for path in (project / ".agents", destination, project / ".agents" / "skill-backups"):
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise ValueError(f"Project installation path is not a regular directory: {path}")

    original: dict[str, dict[str, str] | None] = {}
    changed: list[str] = []
    unchanged: list[str] = []
    for name in PROFILES[profile]:
        target = destination / name
        if target.is_symlink() or target.exists() and not target.is_dir():
            raise ValueError(f"Existing skill is not a regular directory: {name}")
        before = inventory(target) if target.exists() else None
        original[name] = before
        if before == inventory(package / "skills" / name):
            unchanged.append(name)
        elif before is not None and not upgrade:
            raise ValueError(f"Existing different skill preserved: {name}. Use --upgrade to back it up and replace it")
        else:
            changed.append(name)

    result: dict[str, object] = {
        "profile": profile, "project": str(project), "skills": list(PROFILES[profile]),
        "unchanged": unchanged, "changed": changed, "check_only": check,
        "backup": None, "account_connection": "NOT_CHANGED",
    }
    if check or not changed:
        return result

    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    backup = project / ".agents" / "skill-backups" / stamp
    installed: list[Path] = []
    saved: list[tuple[Path, Path]] = []
    with tempfile.TemporaryDirectory(prefix="analysis-skill-stage-", dir=project / ".agents") as temporary:
        stage = Path(temporary)
        for name in changed:
            shutil.copytree(package / "skills" / name, stage / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        try:
            for name in changed:
                target = destination / name
                if target.is_symlink():
                    raise ValueError(f"Skill changed during installation: {name}")
                current = inventory(target) if target.exists() and target.is_dir() else None
                if current != original[name] or target.exists() and not target.is_dir():
                    raise ValueError(f"Skill changed during installation: {name}")
                if target.exists():
                    backup.mkdir(parents=True, exist_ok=True)
                    archived = backup / name
                    target.rename(archived)
                    saved.append((archived, target))
                (stage / name).rename(target)
                installed.append(target)
        except (OSError, ValueError):
            for target in reversed(installed):
                shutil.rmtree(target)
            for archived, target in reversed(saved):
                archived.rename(target)
            raise
    if saved:
        result["backup"] = str(backup)
    result["discovery"] = "Open this project in Codex and verify the selected skills; restart Codex if they do not appear"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Target customer project")
    parser.add_argument("--profile", choices=PROFILES, default="standard")
    parser.add_argument("--upgrade", action="store_true", help="Back up and replace different same-name skills")
    parser.add_argument("--check", action="store_true", help="Validate package and proposed install without writing")
    parser.add_argument("--list", action="store_true", help="Show installation choices")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.exit(2, "Python 3.11 or newer is required.\n")
    if args.list:
        print(json.dumps(PROFILES, ensure_ascii=False, indent=2))
        return
    if args.project is None:
        parser.error("--project is required unless --list is used")
    try:
        result = install(Path(__file__).resolve().parent, args.project, args.profile,
                         upgrade=args.upgrade, check=args.check)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Installation stopped: {exc}\n")


if __name__ == "__main__":
    main()
