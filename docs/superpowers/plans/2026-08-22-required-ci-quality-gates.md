# Required CI Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add required GitHub Actions checks and bring the repository's formatting, lint, and type-check baselines to zero failures.

**Architecture:** CI uses Python 3.11 and `uv`, installs the SDK plus public API runtime dependencies without requiring the unavailable sibling AXIOM checkout, and validates isolated source paths. `ruff` owns formatting and linting; `mypy` validates the complete API and SDK source trees with concrete collaborator protocols and correct local contracts.

**Tech Stack:** GitHub Actions, Python 3.11, uv, Ruff 0.12.12, mypy 1.15.0, pytest 8.3.5.

---

### Task 1: Establish a clean quality baseline

**Files:**
- Modify: Python files reported by `ruff format --check .`
- Modify: `packages/sdk/src/data_intelligence_sdk/scheduled_specs/worker.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/core/types.py`

- [x] **Step 1: Capture the failing baseline**

Run: `uvx ruff@0.12.12 format --check . && uvx ruff@0.12.12 check . && uvx --from mypy==1.15.0 mypy --ignore-missing-imports --follow-imports=skip packages/sdk/src packages/api/src`

Expected: Formatting, one unused import, and static contract failures are reported.

- [x] **Step 2: Normalize formatting and remove the unused import**

Run: `uvx ruff@0.12.12 format . && uvx ruff@0.12.12 check --fix .`

- [x] **Step 3: Remove the stale `TYPE_CHECKING` import of `IntentAnalysis`**

```python
if TYPE_CHECKING:
    from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
```

The locally declared `IntentAnalysis` is the public pipeline contract and must
not be shadowed by the older analyzer-specific type.

- [x] **Step 4: Verify the formatter and linter pass**

Run: `uvx ruff@0.12.12 format --check . && uvx ruff@0.12.12 check .`

Expected: Exit code 0.

### Task 2: Correct source-level type contracts

**Files:**
- Modify: `packages/sdk/src/data_intelligence_sdk/runtime/event_payload.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/runtime/sandbox.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/runtime/deep_agent_backend.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/core/pipeline.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/spec/llm_builder.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/spec/prompts/*.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/engines/general.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/tools/execution.py`
- Modify: `packages/api/src/data_intelligence_api/application/workflow.py`
- Modify: `packages/api/src/data_intelligence_api/infrastructure/workflow/pipeline_factory.py`

- [x] **Step 1: Reproduce the type failures**

Run: `uvx --from mypy==1.15.0 mypy --ignore-missing-imports --follow-imports=skip packages/sdk/src packages/api/src`

Expected: Existing errors identify concrete `object` collaborators, incompatible dynamic mappings, and untyped helper variables.

- [x] **Step 2: Add or use focused protocol/`Any` boundaries only where runtime collaborators are dynamically loaded**

Keep typed domain values (`IntentAnalysis`, execution specs, runtime contexts)
strict. Use `Any` only at boundaries that intentionally accept third-party
sandbox, LLM, or engine implementations.

- [x] **Step 3: Correct helper local annotations and required method arguments**

Use independent names or `Any` for polymorphic preview values, guard optional
mappings before access, and supply required LLM stage arguments.

- [x] **Step 4: Verify the complete type check passes**

Run: `uvx --from mypy==1.15.0 mypy --ignore-missing-imports --follow-imports=skip packages/sdk/src packages/api/src`

Expected: `Success: no issues found`.

### Task 3: Add the required GitHub Actions workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [x] **Step 1: Add formatting, lint, type, and test jobs**

The workflow must trigger on `push` and `pull_request` to `main`, use
`actions/checkout@v4`, `astral-sh/setup-uv@v6`, and Python 3.11. Jobs run:

```yaml
uvx ruff@0.12.12 format --check .
uvx ruff@0.12.12 check .
uvx --from mypy==1.15.0 mypy --ignore-missing-imports --follow-imports=skip packages/sdk/src packages/api/src
```

The test job installs the SDK with `uv run --no-project --with ./packages/sdk`
plus public API dependencies, exports the API and SDK source directories through
`PYTHONPATH`, and excludes only
`packages/api/tests/test_stateless_genreport_cutover.py` because it needs the
unavailable sibling `GenReport` checkout.

- [x] **Step 2: Validate workflow YAML and each local CI command**

Run: `uv run --project packages/sdk --with pyyaml==6.0.2 python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` followed by the exact format, lint, type, and test commands in the workflow.

Expected: Every command exits 0.

- [x] **Step 3: Commit locally**

```bash
git add .github/workflows/ci.yml packages docs
git commit -m "ci: add required quality gates"
```

Do not push.
