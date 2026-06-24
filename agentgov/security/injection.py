"""Defender-style prompt-injection / jailbreak detection (Agent 365 Phase 3).

A lightweight, dependency-free heuristic that flags common prompt-injection and
jailbreak attempts in user input before it reaches the model. It is intentionally
conservative (signals + a score), mirroring how Microsoft Defender surfaces
risky agent interactions — the agent decides whether to block or escalate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (signal id, severity weight, compiled pattern)
_PATTERNS: tuple[tuple[str, int, re.Pattern], ...] = (
    ("ignore_previous", 3, re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\b", re.I)),
    ("disregard_instructions", 3, re.compile(r"\bdisregard\s+(?:the\s+)?(?:previous|prior|above|instructions?|rules?)\b", re.I)),
    ("override_system", 3, re.compile(r"\b(?:override|bypass|forget)\s+(?:the\s+)?(?:system|previous|safety|rules?|guard)\b", re.I)),
    ("reveal_system_prompt", 3, re.compile(r"\b(?:reveal|show|print|repeat|leak)\b[^.\n]{0,40}\b(?:system\s+prompt|instructions?|your\s+rules?)\b", re.I)),
    ("new_persona", 2, re.compile(r"\byou\s+are\s+now\b|\bact\s+as\b|\bpretend\s+to\s+be\b", re.I)),
    ("jailbreak_alias", 3, re.compile(r"\b(?:DAN|do\s+anything\s+now|developer\s+mode|jailbreak)\b", re.I)),
    ("exfiltrate", 2, re.compile(r"\b(?:send|email|post|upload|exfiltrate)\b[^.\n]{0,30}\b(?:to|http|@)\b", re.I)),
)

_BLOCK_THRESHOLD = 3


@dataclass(frozen=True)
class InjectionSignal:
    id: str
    weight: int
    span: str


@dataclass(frozen=True)
class InjectionResult:
    signals: tuple[InjectionSignal, ...]

    @property
    def score(self) -> int:
        return sum(s.weight for s in self.signals)

    @property
    def detected(self) -> bool:
        return bool(self.signals)

    @property
    def should_block(self) -> bool:
        return self.score >= _BLOCK_THRESHOLD

    @property
    def signal_ids(self) -> list[str]:
        seen: list[str] = []
        for signal in self.signals:
            if signal.id not in seen:
                seen.append(signal.id)
        return seen


def detect_prompt_injection(text: str) -> InjectionResult:
    if not text:
        return InjectionResult(())
    signals: list[InjectionSignal] = []
    for signal_id, weight, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            signals.append(InjectionSignal(id=signal_id, weight=weight, span=match.group(0)))
    return InjectionResult(tuple(signals))
