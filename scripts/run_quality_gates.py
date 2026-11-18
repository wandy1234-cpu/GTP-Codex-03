"""Run repository quality gates for CI/smoke validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

try:
    from quant_alpha.config import ProjectPaths
except ModuleNotFoundError:
    src_root = Path(__file__).resolve().parents[1] / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from quant_alpha.config import ProjectPaths


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*=\s*['\"][^'\"]{12,}['\"]"),
    re.compile(r"\b[a-f0-9]{40,}\b", re.IGNORECASE),
]

SKIP_DIRS = {".git", "__pycache__", "data", "models", "reports"}


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".json"}:
            continue
        yield path


def scan_for_secrets(root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "Do not commit tokens" in line:
                continue
            for pat in SECRET_PATTERNS:
                if pat.search(line):
                    findings.append({"file": str(path.relative_to(root)), "line": str(lineno), "kind": "possible_secret"})
                    break
    return findings


def latest_quality_gate(paths: ProjectPaths) -> dict:
    files = sorted(paths.report_dir.glob("quality_gate_*.json"))
    if not files:
        return {"status": "missing", "reason": "no quality_gate_*.json artifact found"}
    latest = files[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["artifact"] = str(latest)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Quant Alpha quality gates.")
    parser.add_argument("--write-report", action="store_true", help="Write reports/ci_quality_gates.json.")
    parser.add_argument("--allow-missing-model-gate", action="store_true", help="Do not fail when no daily quality gate exists.")
    args = parser.parse_args()

    root = Path.cwd()
    paths = ProjectPaths(root)
    secret_findings = scan_for_secrets(root)
    model_gate = latest_quality_gate(paths)

    checks = {
        "secret_scan": {"status": "pass" if not secret_findings else "fail", "findings": secret_findings},
        "model_quality_gate": model_gate,
    }
    failed = []
    if secret_findings:
        failed.append("secret_scan")
    if model_gate.get("status") == "fail" or (model_gate.get("status") == "missing" and not args.allow_missing_model_gate):
        failed.append("model_quality_gate")

    report = {"status": "pass" if not failed else "fail", "failed_checks": failed, "checks": checks}
    if args.write_report:
        paths.ensure()
        out = paths.report_dir / "ci_quality_gates.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(out)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
