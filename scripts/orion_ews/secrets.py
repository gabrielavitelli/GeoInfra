"""Load ORION_USER_KEY from env or GCP Secret Manager without printing the value."""

from __future__ import annotations

import os
import subprocess
from typing import Optional

DEFAULT_PROJECT = "climaticsystem"
DEFAULT_SECRET = "orion-user-key"


def load_orion_user_key(
    *,
    project: str = DEFAULT_PROJECT,
    secret: str = DEFAULT_SECRET,
    version: str = "latest",
    allow_missing: bool = False,
) -> Optional[str]:
    """Return User Key length-checked; never logs the payload.

    Order:
      1. ORION_USER_KEY / ORION_API_KEY env
      2. gcloud secrets versions access
    """
    for name in ("ORION_USER_KEY", "ORION_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            os.environ["ORION_USER_KEY"] = val  # normalize
            return val

    cmd = [
        "gcloud",
        "secrets",
        "versions",
        "access",
        version,
        f"--secret={secret}",
        f"--project={project}",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        if allow_missing:
            return None
        raise RuntimeError("gcloud não encontrado no PATH") from e
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip()
        # Never include secret material; gcloud errors are safe.
        if allow_missing:
            return None
        raise RuntimeError(f"falha ao ler secret {secret}@{project}: {err[:300]}") from e

    val = (proc.stdout or "").strip()
    if not val:
        if allow_missing:
            return None
        raise RuntimeError(f"secret {secret} veio vazio")
    os.environ["ORION_USER_KEY"] = val
    return val


def key_fingerprint(val: str) -> str:
    """Safe log line: length only + first/last 2 chars."""
    if not val:
        return "len=0"
    if len(val) < 6:
        return f"len={len(val)}"
    return f"len={len(val)} head={val[:2]}… tail=…{val[-2:]}"
