# PromptOps Templates

## Feature Or Refactor

Prompt version: `feature_refactor_v1`

```text
You are working inside the Quant Alpha repository. Implement {task} with the smallest safe diff.

Context:
- Modules: {modules}
- Expected behavior: {expected_behavior}
- Constraints: {constraints}

Required output:
- Code changes and focused tests where practical.
- Verification commands.
- Short risk and rollback notes.

Hard rules:
- Do not expose or add secrets.
- Do not add dependencies unless the reason and license are clear.
- Preserve strict time ordering for model training and validation.
```

## Bug Fix

Prompt version: `bug_fix_v1`

```text
Fix {bug} in Quant Alpha.

Before editing:
- Identify the smallest failing path.
- Prefer mock or fixture data over live network calls.

After editing:
- Add or update a regression check.
- Run compileall and the relevant smoke or quality gate command.

Return:
- What changed.
- Why the bug happened.
- How it was verified.
```

## Model Gate Review

Prompt version: `model_gate_review_v1`

```text
Review the latest Quant Alpha model artifacts and decide whether the challenger is safe to promote.

Inputs:
- reports/quality_gate_*.json
- reports/drift_*.json
- reports/walkforward_summary_*.json
- reports/backtest_*.parquet
- reports/governance/governance_history.jsonl

Decision criteria:
- Walk-forward folds are sufficient and strictly time ordered.
- RankIC and fold win rate meet thresholds.
- Drawdown and turnover are not materially worse.
- Drift alerts and data warnings are explainable.

Return JSON:
{
  "decision": "promote|reject|hold",
  "reasons": [],
  "required_followups": [],
  "rollback_trigger": ""
}
```

## Runbook

Prompt version: `runbook_v1`

```text
Write a Chinese runbook for {workflow}.

Include:
- Purpose and boundaries.
- Commands and key parameters.
- Artifacts and important fields.
- Common failure modes and triage.
- Security and compliance notes.

Do not include tokens, keys, cookies, or private data.
```
