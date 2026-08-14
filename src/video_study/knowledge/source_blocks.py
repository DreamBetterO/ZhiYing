from __future__ import annotations

from ..transcript import merge_transcript_segments
from ..utils import hhmmss
from .course_ir import build_source_blocks


def build_cloud_source_blocks(transcript: dict) -> tuple[str, dict[str, list[str]]]:
    rows = build_source_blocks(transcript)
    mapping = {row.source_id: list(row.segment_ids) for row in rows}
    text = "\n".join(
        f"[{row.source_id}; time:{hhmmss(row.start_seconds)}-{hhmmss(row.end_seconds)}] {row.text}"
        for row in rows
    )
    return text, mapping


def build_cloud_source(transcript: dict) -> str:
    return build_cloud_source_blocks(transcript)[0]
