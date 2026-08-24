# Project Instructions — 知影 Video Summarization

Python desktop application for video-to-document summarization. Pipeline: JSON → Markdown → Word → PDF. Uses one VLM session per video. Conda env: `ImageT10`.

## Build & Test

- Activate env: `conda activate ImageT10` (located at `D:\Anaconda\envs\envs\ImageT10`)
- Unit tests: `unittest discover -s tests`
- Compile check: `compileall -q src tests`
- Dependency check: `pip check`
- Diff check: `git diff --check`

## Session Startup (Required)

1. Read `docs/项目索引.md` for current baseline, version, and hand-off entry.
2. Read `docs/AI执行入口.md` for immediate actions and boundaries.
3. Read `docs/迭代升级/当前架构升级状态.yaml` if the task touches architecture or pipeline contracts.
4. Confirm authorizations — cloud requests, model downloads, dependency reinstalls, portable release builds, and long-video reruns each require separate authorization.

## Cognitive Discipline

- **Read before doing**: review entry documents and owning modules before modifying any file. When conflicts arise, trust machine-checkable contracts (`docs/architecture/*.yaml`, `docs/diagnostics/problem-index.yaml`), code, tests, and execution facts over memory.
- **Version timeline** (`docs/迭代升级/迭代记录与问题.md`): append one entry only after user approves a named version — Asia/Shanghai time, version label/type, user-raised problem, approved solution. No AI reasoning or implementation details. Existing entries are immutable.
- **`docs/项目索引.md`**: keep only the latest confirmed runnable status; delete the previous entry when writing a new one. Historical delivery details go in `docs/迭代升级/执行事实.yaml`.
- **Execution documents** (reasoning/architecture/implementation/testing/hand-off): place under `docs/迭代升级/` (compact YAML/JSON acceptable). Do not copy into the version timeline.

## Execution Discipline

- **Diagnosis first**: run `.venv\Scripts\python.exe scripts\diagnostics\diagnose_workspace.py workspace\<video_id>` (read-only), then locate failures by `step_id/error_code` in `docs/diagnostics/problem-index.yaml`.
- **Test-first, minimal change**: add a failing regression test before modifying the smallest responsible module. Tasks in the same directory must run serially — no concurrency to mask write races.
- **Cache priority**: do not rerun long videos without reason; diagnose existing workspaces before cleaning.
- **Link integrity**: validate via the UI-logic equivalent path, not just CLI.
- **Clean code**: no hardcoding, magic values, global variables, or redundancy.
- **Thinking docs**: place iteration-thinking documents in `docs/迭代升级/交互思考/`.
- **Timestamp accuracy**: ensure document timestamps are correct.

## Boundaries (Do Not Violate)

- **Secrets**: never read, print, or commit `.env`.
- **Cloud inference opt-in**: before each real cloud request, explain data/endpoint/model chain/budget and obtain explicit authorization. Never make live requests merely to test config.
- **`video-study://` protocol**: invokes local player for positioning; does not open web pages.
- **Docs scope**: `docs/` holds development documents; update business documents only when the user requests.
- **Data/source separation**: `Resource/`, `workspace/`, `output/`, `models/`, `视频/` are data artifacts. One-off scripts go in `./tmp`.
- **Dirty worktree**: preserve the user's worktree; do not assume the repository can be reset or committed.
- **Python environment**: use conda `ImageT10`; do not modify core dependencies unless necessary.

## Architecture Contracts

- Preserve JSON → Markdown → Word → PDF data flow and timestamp traceability.
- Desktop-only: no Web UI, local HTTP service, port listener, or `serve` command.
- Course IR, one VLM session per video, compact CloudPayload, Canonical Document v2, task progress, and Application/Desktop split are stable contracts unless the user explicitly approves a major version change.
- Legacy compatibility is one-way: v1 artifacts are read-only migration inputs; new documents are Document v2 only; historical workspaces are not proactively deleted.

## Communication

- Communicate with the user in Chinese unless they request otherwise.
- State concrete evidence, affected files, and validation results. Do not obscure real failures by beautifying output.

## Acceptance and Wrap-up

- Run the complete offline acceptance suite (see Build & Test above).
- Structured wrap-up: what was done (with paths), key findings, remaining gaps/blockers, items needing user decision.
- On budget or context warnings, converge; do not start unrelated tasks.
- For small feature upgrades, test the upgraded part specifically; mind test and acceptance boundaries.
- For full-chain testing requests, align tests with UI logic, run UI-mounted full-chain tests, and do not beautify chain artifacts.
