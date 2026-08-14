# Project continuation instructions

Current baseline: **V4.0 architecture upgrade complete** / product version `0.4.0`.

The production path is the 15-step `StepRegistry -> PipelineRunner -> ArtifactStore/WorkspaceCache` execution kernel. `pipeline.py` is only the public compatibility facade. Course IR, one VLM session per video, compact CloudPayload, Canonical Document v2, task ETA, and the Application/Desktop split are the only default contracts.

Before changing this project, read `CODELY.md` for project context, then `MEMORY.md` and `迭代升级/AI执行入口.md` linked from it.

- Communicate with the user in Chinese unless they request otherwise.
- Never print, copy, or commit secrets from `.env`.
- Keep cloud inference opt-in. Before every real cloud request, explain the data, endpoint, model chain, and budget, then obtain the user's explicit authorization. Never make a live API request merely to test configuration.
- Use the existing 15-step resumable execution kernel and preserve the JSON -> Markdown -> Word -> PDF data flow and timestamp traceability.
- Keep the product desktop-only. Do not add a Web UI, local HTTP service, port listener, or `serve` command.
- Preserve Word/PDF source playback through the `video-study://` local protocol; it must not open a webpage.
- Keep legacy compatibility one-way: v1 artifacts are read-only migration inputs, new documents are written only as Document v2, and historical workspaces are not proactively deleted.
- Treat files under `Resource/`, `workspace/`, `output/`, `models/`, and `视频/` as local data/artifacts rather than source code.
- When you need to write code unrelated to the main project, such as test scripts, place them inside the `./tmp` folder.

## Diagnosis and validation

- Diagnose a workspace read-only with `.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`.
- Locate failures by `step_id/error_code` in `docs/diagnostics/problem-index.yaml`, then use `docs/architecture/module-boundaries.yaml` and `docs/architecture/pipeline-steps.yaml` to identify the owning module and affected contracts.
- Add a focused regression test before changing the smallest responsible module.
- Finish with the complete offline acceptance suite. Real cloud requests, model downloads, dependency reinstalls, and long-video reruns require separate authorization.

## Iteration records

Before changing this project, read `迭代升级/AI执行入口.md`. Read `迭代升级/迭代记录与问题.md` only to understand the user-approved solution history.

The timeline already contains the single user-approved V4.0 entry. V4.0 implementation, validation, or hand-off work must not append another entry or modify the existing entry.

`迭代记录与问题.md` is the user's solution-version timeline. Its write gate is strict:

- A record starts with problems discovered by the user in the main conversation and ends only when the user explicitly approves them as a named upgrade version.
- Before that explicit approval, do not write or draft an entry in this file.
- After approval, append exactly one entry for that version. Do not create separate proposal, implementation, validation, environment or hand-off entries for the same version.
- Each entry contains only: Asia/Shanghai time, user-approved version label/type, problems raised by the user, and the final solution approved by the user.
- Do not write AI-discovered issues, reasoning traces, research notes, rejected alternatives, environment details, file lists, task plans, test results or remaining Agent work into this timeline.
- Existing version entries are append-only and immutable. A later problem/solution cycle becomes a new entry only after the user approves the next version.

Execution documents are separate. AI reasoning, architecture detail, environment constraints, implementation stages, file-level tasks, tests and hand-off notes may be maintained under `迭代升级/` as needed for efficient and accurate work. Markdown is not required; use compact YAML/JSON/text when that is easier for Agents to query. Create or keep only information that prevents repeated investigation or directly improves execution, link useful files from `AI执行入口.md`, and remove stale duplication. These files do not require a timeline entry and must never be copied into `迭代记录与问题.md` unless the user later approves their product problem/solution as a new version.

Version classification may be suggested by an Agent but is finalized by the user:

- **Small/minor**: backward-compatible fixes/features that keep the main stage order, primary contracts and desktop boundary.
- **Major**: changes to the core stage order, primary contracts, model responsibility boundary, compatibility strategy or desktop product boundary; include migration and rollback in the execution document.
