from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import P2AError


@dataclass(frozen=True)
class GitFacts:
    head_sha: str
    branch: str
    clean: bool
    changed_files: tuple[str, ...]


class GitInspector:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _run(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise P2AError("GIT_FACT_READ_FAILED", "git command failed", args=list(args), stderr=proc.stderr.strip())
        return proc.stdout.strip()

    def head_sha(self) -> str:
        return self._run("rev-parse", "HEAD")

    def branch(self) -> str:
        return self._run("branch", "--show-current")

    def clean(self) -> bool:
        return not self._run("status", "--porcelain=v1")

    def changed_files(self, base_sha: str) -> tuple[str, ...]:
        output = self._run("diff", "--name-only", f"{base_sha}...HEAD")
        return tuple(sorted(filter(None, output.splitlines())))

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            return False
        raise P2AError("GIT_FACT_READ_FAILED", "git ancestry check failed", stderr=proc.stderr.decode().strip())

    def facts(self, base_sha: str) -> GitFacts:
        return GitFacts(self.head_sha(), self.branch(), self.clean(), self.changed_files(base_sha))
