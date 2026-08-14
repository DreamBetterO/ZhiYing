from __future__ import annotations

from pathlib import Path


class FakeProcessPort:
    def __init__(self) -> None:
        self.calls = []

    def run(self, command, **options):
        self.calls.append((tuple(command), options))
        return {"returncode": 0}


class FakeMediaPort:
    def probe(self, _video: Path):
        return {"format": {"duration": "1.0"}}

    def extract_audio(self, _video: Path, output: Path, **_options):
        output.write_bytes(b"audio")
        return output

    def extract_frame_candidates(self, _video: Path, _output: Path, _options, **_runtime):
        return {"frames": []}


class FakeSpeechPort:
    def probe_capability(self):
        return {"available": True}

    def decode(self, _audio: Path, _output: Path, _options, **_runtime):
        return {"segments": []}


class FakeVisionSession:
    def compare(self, _job):
        return {"decision": "no_match"}

    def detail(self, _job):
        return {"visible": False}

    def close(self):
        return None


class FakeVisionPort:
    def preflight(self):
        return {"ok": True}

    def open_session(self, _options):
        return FakeVisionSession()


class FakeCloudJsonPort:
    def __init__(self) -> None:
        self.calls = []

    def request_json(self, payload, *, validator, stage, cancel_check):
        self.calls.append((payload, stage))
        result = {"ok": True}
        validator(result)
        return result

    def request_json_with_info(self, payload, *, validator, stage, cancel_check):
        return self.request_json(
            payload, validator=validator, stage=stage, cancel_check=cancel_check,
        ), {"model": "fake", "attempts": [], "usage": {}}


class FakeDocumentPort:
    def render_markdown(self, _document, output: Path):
        return output

    def render_word(self, _document_json: Path, output: Path, **_runtime):
        return output

    def render_pdf(self, _document, _word: Path, _output: Path, **_runtime):
        return "built_in"
