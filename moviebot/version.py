"""Which version is running, and how old is it?

The Docker image gets the answer baked in at build time (see Dockerfile and the workflow).
A checkout without that – `moviebot serve` on your own machine – asks git instead.
"""

import os
import subprocess
from functools import lru_cache
from importlib import metadata
from pathlib import Path


def _package_version() -> str:
    try:
        return metadata.version("moviebot")
    except metadata.PackageNotFoundError:  # run straight from the source tree
        return "0.0.0"


def _git(*args: str) -> str | None:
    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


@lru_cache(maxsize=1)
def info() -> dict:
    """version: the number (a release tag wins over pyproject.toml); commit: short git hash,
    the same one as in the image tag `sha-<commit>`; released: day of that commit (ISO)."""
    env = os.environ.get
    return {
        "version": env("MOVIEBOT_VERSION") or _package_version(),
        "commit": env("MOVIEBOT_COMMIT") or _git("rev-parse", "--short=7", "HEAD"),
        "released": env("MOVIEBOT_RELEASED") or _git("log", "-1", "--format=%cs"),
    }
