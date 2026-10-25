"""This process's build identity, captured once at import.

Wraps the vendored scaffold helper so `/admin/api/version`, the index ETag and
`peer_health()` read one capture: the SHA and `built_at` can never come from
two different moments, and git is spawned once per process.
"""

from __future__ import annotations

from ..build_info import build_identity

IDENTITY = build_identity()


def git_sha() -> str:
    """Short git SHA this process loaded; a `git pull` on disk does not change it."""
    return IDENTITY["git_sha"]
