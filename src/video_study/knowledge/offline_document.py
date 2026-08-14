from __future__ import annotations

from .adapter import v1_to_v2
from .document import _fallback_document

__all__ = ["build_offline_document"]


def build_offline_document(
    manifest: dict,
    transcript: dict,
    frames: dict,
    settings: dict,
    reason: str | None = None,
) -> dict:
    """构造真实的 no-speech/本地离线 Document v2。"""
    return v1_to_v2(_fallback_document(manifest, transcript, frames, settings, reason))
