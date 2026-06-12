"""Enforces guideline §11.3/§18.2: bug-fix commits need regression tests.

Symptom-only patches usually look small in a diff: a guard clause, a renamed
error, or a branch that makes the immediate failure disappear. This gate checks
the commit range under review. If a commit subject/body says it fixes a bug or
patches a regression, the same commit must also touch `tests/`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "regression_test_per_bugfix.json"
BUGFIX_RE = re.compile(r"\b(bug|bugfix|fix|fixed|fixes|hotfix|patch|regression)\b", re.IGNORECASE)
TEST_PATH_PREFIX = "tests/"


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    body: str
    changed_paths: list[str]


@dataclass(frozen=True)
class RegressionTestFinding:
    code: str
    commit: str
    subject: str
    changed_paths: list[str]
    message: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _run_git(root: Path, args: list[str]) -> str:
    result = subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _git_ref_exists(root: Path, ref: str) -> bool:
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=root,
        capture_output=True,
    )
    return result.returncode == 0


def _default_base_candidates() -> list[str]:
    candidates: list[str] = []
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        candidates.extend([f"origin/{base_ref}", base_ref])
    candidates.extend(["origin/main", "main", "HEAD~1"])
    return candidates


def _default_base_ref(root: Path) -> str | None:
    return next((ref for ref in _default_base_candidates() if _git_ref_exists(root, ref)), None)


def _commit_shas(root: Path, *, base_ref: str | None, head_ref: str) -> list[str]:
    base = base_ref or _default_base_ref(root)
    if base is None:
        return []
    output = _run_git(root, ["rev-list", "--reverse", f"{base}..{head_ref}"])
    return [line for line in output.splitlines() if line]


def _commit_subject_body(root: Path, sha: str) -> tuple[str, str]:
    output = _run_git(root, ["show", "--no-patch", "--format=%s%n%b", sha])
    lines = output.splitlines()
    subject = lines[0] if lines else ""
    body = "\n".join(lines[1:]).strip()
    return subject, body


def _commit_changed_paths(root: Path, sha: str) -> list[str]:
    output = _run_git(root, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha])
    return sorted(line for line in output.splitlines() if line)


def _commit_info(root: Path, sha: str) -> CommitInfo:
    subject, body = _commit_subject_body(root, sha)
    return CommitInfo(sha=sha, subject=subject, body=body, changed_paths=_commit_changed_paths(root, sha))


def _is_bugfix_commit(commit: CommitInfo) -> bool:
    return bool(BUGFIX_RE.search(f"{commit.subject}\n{commit.body}"))


def _has_regression_test(commit: CommitInfo) -> bool:
    return any(path.startswith(TEST_PATH_PREFIX) for path in commit.changed_paths)


def collect_commits(root: Path = ROOT, *, base_ref: str | None = None, head_ref: str = "HEAD") -> list[CommitInfo]:
    return [_commit_info(root, sha) for sha in _commit_shas(root, base_ref=base_ref, head_ref=head_ref)]


def collect_findings(
    root: Path = ROOT,
    *,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
) -> list[RegressionTestFinding]:
    findings: list[RegressionTestFinding] = []
    for commit in collect_commits(root, base_ref=base_ref, head_ref=head_ref):
        if not _is_bugfix_commit(commit) or _has_regression_test(commit):
            continue
        findings.append(
            RegressionTestFinding(
                code="bugfix_commit_missing_regression_test",
                commit=commit.sha,
                subject=commit.subject,
                changed_paths=commit.changed_paths,
                message="Bug-fix/patch commit must include a regression test change under tests/",
            )
        )
    return findings


def write_report(
    output: Path,
    findings: list[RegressionTestFinding],
    *,
    root: Path = ROOT,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    commits = collect_commits(root, base_ref=base_ref, head_ref=head_ref)
    bugfix_commits = [commit for commit in commits if _is_bugfix_commit(commit)]
    report = {
        "gate": "regression_test_per_bugfix",
        "baseline": 0,
        "head_ref": head_ref,
        "commit_count": len(commits),
        "bugfix_commit_count": len(bugfix_commits),
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[RegressionTestFinding], output: Path) -> None:
    print("Release gate blocked: bug-fix/patch commits need regression tests.")
    for finding in findings:
        short_sha = finding.commit[:12]
        print(f"- {short_sha} {finding.subject}: no tests/ change in the same commit")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Require regression tests on bug-fix commits.")
    parser.add_argument("--base-ref", default=None)
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(base_ref=args.base_ref, head_ref=args.head_ref)
    write_report(args.output, findings, base_ref=args.base_ref, head_ref=args.head_ref)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Regression-test-per-bugfix gate passed: bug-fix commits include tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
