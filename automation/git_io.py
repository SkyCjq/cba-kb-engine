from __future__ import annotations

import subprocess
import json
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


class GitHubInspector:
    """Collect PR and CI facts from authenticated GitHub Code Truth."""

    def __init__(self, repository: str) -> None:
        self.repository = repository

    def _json(self, *args: str):
        try:
            proc = subprocess.run(["gh", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except OSError as exc:
            raise P2AError("GITHUB_FACTS_UNAVAILABLE", "GitHub CLI is unavailable", error=type(exc).__name__) from exc
        if proc.returncode:
            raise P2AError("GITHUB_FACTS_UNAVAILABLE", "GitHub facts could not be collected", args=list(args), stderr=proc.stderr.strip()[:300])
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise P2AError("GITHUB_FACTS_UNAVAILABLE", "GitHub returned invalid JSON") from exc

    def collect(self, pr_number: int, workflow_name: str, head_sha: str) -> dict:
        pr = self._json(
            "pr", "view", str(pr_number), "--repo", self.repository,
            "--json", "number,url,state,baseRefOid,headRefOid",
        )
        runs = self._json(
            "api", f"repos/{self.repository}/actions/runs?head_sha={head_sha}&per_page=100",
        ).get("workflow_runs", [])
        matching = [
            {"id": item["id"], "name": item["name"], "head_sha": item["head_sha"], "status": item["status"], "conclusion": item["conclusion"], "event": item["event"], "html_url": item["html_url"]}
            for item in runs
            if item.get("name") == workflow_name and item.get("head_sha") == head_sha
        ]
        if not any(item["status"] == "completed" and item["conclusion"] == "success" for item in matching):
            raise P2AError("CI_NOT_GREEN", "No successful exact-head workflow run found", workflow_name=workflow_name, head_sha=head_sha)
        return {"pr": pr, "runs": matching}
