# Project continuation instructions

Before changing this project, read `MEMORY.md` and the project note linked from it.

- Communicate with the user in Chinese unless they request otherwise.
- Never print, copy, or commit secrets from `.env` or `QwenAPI.txt`.
- Keep cloud inference opt-in. Do not make a live API request merely to test configuration without telling the user that it will consume quota.
- Prefer the existing resumable pipeline and preserve the JSON -> Markdown -> Word -> PDF data flow and timestamp traceability.
- Keep the product desktop-only. Do not add a Web UI, local HTTP service, port listener, or `serve` command.
- Preserve Word/PDF source playback through the `video-study://` local protocol; it must not open a webpage.
- Treat files under `Resource/`, `workspace/`, `output/`, and `models/` as local data/artifacts rather than source code.
