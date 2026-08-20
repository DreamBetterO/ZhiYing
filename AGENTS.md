# Project continuation instructions

This file is the stable working agreement for AI agents. It should not be treated as a release note or iteration baseline. Current product status, active hand-off facts, and version-specific notes live in `docs/项目索引.md` and `docs/AI执行入口.md`.

`docs/项目索引.md` write gate:
- Only the latest confirmed runnable status is kept; writing a new entry deletes the previous one.
- Historical delivery details belong in `docs/迭代升级/执行事实.yaml`, not in 项目索引.md.
- The entry must state: product version, validation result (test count / compileall / git diff --check), and pending authorizations.

## Required reading before changes

Before changing this project:

1. Read `docs/项目索引.md` to confirm the current baseline, version, and hand-off entry.
2. Read `docs/AI执行入口.md` to confirm immediate actions and boundaries.
3. When needed, read `docs/项目索引.md` and `docs/迭代升级/当前架构升级状态.yaml`.
4. Confirm task boundaries and available authorizations — cloud requests, model downloads, dependency reinstalls, portable release builds, and long-video reruns each require separate authorization.
5. Read before doing: review the entry documents and owning module before modifying any file. When conflicts arise, trust machine-checkable contracts (`docs/architecture/*.yaml`, `docs/diagnostics/problem-index.yaml`), code, tests, and execution facts over memory.
6. If diagnosing a pipeline failure, use the machine-checkable documents linked from those files instead of relying on memory.

Read `docs/迭代升级/迭代记录与问题.md` only when you need the user-approved version history. Do not use it as an execution scratchpad.

## Communication

- Communicate with the user in Chinese unless they request otherwise.
- State concrete evidence, affected files, and validation results.
- Do not obscure real failures by beautifying output or treating broken artifacts as acceptable.

## Product and architecture boundaries

- Preserve the JSON -> Markdown -> Word -> PDF data flow and timestamp traceability.
- Preserve Word/PDF source playback through the `video-study://` local protocol;
- Keep the product desktop-only. Do not add a Web UI, local HTTP service, port listener, or `serve` command.
- Course IR, one VLM session per video, compact CloudPayload, Canonical Document v2, task progress, and the Application/Desktop split are stable contracts unless the user explicitly approves a major version change.
- Keep legacy compatibility one-way: v1 artifacts are read-only migration inputs, new documents are written only as Document v2, and historical workspaces are not proactively deleted.

## Data, secrets, and local artifacts

- Never print, copy, or commit secrets from `.env`.
- Keep cloud inference opt-in. Before every real cloud request, explain the data, endpoint, model chain, and budget, then obtain the user's explicit authorization.
- Never make a live API request merely to test configuration.
- Treat files under `Resource/`, `workspace/`, `output/`, `models/`, and `视频/` as local data/artifacts rather than source code.
- Place development-related documents in `docs/`; update business documents only when the user requests it.
- Preserve the user's dirty worktree; do not assume the repository can be reset or committed.
- When writing code unrelated to the main project, such as one-off test scripts, place it inside `./tmp`.

## Diagnosis and validation

- Diagnose a workspace read-only with `.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`.
- Locate failures by `step_id/error_code` in `docs/diagnostics/problem-index.yaml`.
- Use `docs/architecture/module-boundaries.yaml` and `docs/architecture/pipeline-steps.yaml` to identify the owning module and affected contracts.
- Add a focused regression test before changing the smallest responsible module; run tasks in the same directory serially, do not use concurrency to mask write races.
- Prefer cache: do not rerun long videos without reason; diagnose existing workspaces before deciding to clean them.
- Link integrity: validate via the UI logic equivalent path, not just CLI.
- Clean code: no hardcoding, magic values, global variables, or redundancy.
- Finish code changes with the complete offline acceptance suite.
- Real cloud requests, model downloads, dependency reinstalls, portable release builds, and long-video reruns require separate authorization.
- The development Python environment is conda `ImageT10`; use `conda activate ImageT10` when conda activation is required (located at `D:\Anaconda\envs\envs\ImageT10`).
- Documents produced during iteration thinking may be placed in `docs/迭代升级/交互思考/`.

## Wrap-up and acceptance

- Structure the completion report: what was done (with paths), key findings, remaining gaps/blockers, and items needing user decision.
- When receiving budget or context warnings, converge and do not start unrelated tasks.
- For small feature upgrades, test the upgraded part specifically; mind the test and acceptance boundaries.
- When the user requests full-chain testing, align tests with UI logic, run UI-mounted full-chain tests, and do not beautify chain artifacts.

## Iteration records

`docs/迭代升级/迭代记录与问题.md` is the user's solution-version timeline. Its write gate is strict:

- A record starts with problems discovered by the user in the main conversation and ends only when the user explicitly approves them as a named upgrade version.
- Before that explicit approval, do not write or draft an entry in this file.
- After approval, append exactly one entry for that version.
- Do not create separate proposal, implementation, validation, environment, or hand-off entries for the same version.
- Each entry contains only: Asia/Shanghai time, user-approved version label/type, problems raised by the user, and the final solution approved by the user.
- Do not write AI-discovered issues, reasoning traces, research notes, rejected alternatives, environment details, file lists, task plans, test results, or remaining Agent work into this timeline.
- Existing version entries are append-only and immutable.

Execution documents are separate. AI reasoning, architecture detail, environment constraints, implementation stages, file-level tasks, tests, and hand-off notes may be maintained under `docs/迭代升级/` as needed for efficient and accurate work. Create or keep only information that prevents repeated investigation or directly improves execution, link useful files from `docs/AI执行入口.md`, and remove stale duplication.

