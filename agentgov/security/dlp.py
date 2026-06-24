"""DLP engine — detect, redact, and decide on sensitive info in agent I/O.

The runtime counterpart to a Microsoft Purview DLP policy: it scans text against
the sensitive information types in ``governance/security/dlp-policy.yaml`` and,
based on the agent's declared data sensitivity, returns an ``audit`` / ``redact``
/ ``block`` verdict plus a redacted copy of the text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .policy import DlpPolicy


class DlpVerdict(str, Enum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


@dataclass(frozen=True)
class DlpFinding:
    info_type: str
    severity: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class DlpResult:
    verdict: DlpVerdict
    findings: tuple[DlpFinding, ...]
    redacted_text: str

    @property
    def finding_types(self) -> list[str]:
        # Preserve first-seen order, de-duplicated.
        seen: list[str] = []
        for finding in self.findings:
            if finding.info_type not in seen:
                seen.append(finding.info_type)
        return seen

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)


class DlpEngine:
    def __init__(self, policy: DlpPolicy) -> None:
        self._policy = policy

    def scan(self, text: str) -> list[DlpFinding]:
        if not text:
            return []
        findings: list[DlpFinding] = []
        for info_type in self._policy.info_types:
            for match in info_type.regex().finditer(text):
                findings.append(
                    DlpFinding(
                        info_type=info_type.id,
                        severity=info_type.severity,
                        start=match.start(),
                        end=match.end(),
                        text=match.group(0),
                    )
                )
        findings.sort(key=lambda f: (f.start, f.end))
        return findings

    @staticmethod
    def redact(text: str, findings: Iterable[DlpFinding]) -> str:
        # Apply right-to-left so earlier spans keep their offsets.
        ordered = sorted(findings, key=lambda f: f.start, reverse=True)
        redacted = text
        for finding in ordered:
            placeholder = f"[REDACTED:{finding.info_type}]"
            redacted = redacted[: finding.start] + placeholder + redacted[finding.end :]
        return redacted

    def inspect(self, text: str, sensitivity: str | None) -> DlpResult:
        """Scan ``text`` and decide the verdict for the given data sensitivity."""
        findings = self.scan(text)
        if not findings:
            return DlpResult(DlpVerdict.ALLOW, (), text)

        action = self._policy.action_for(sensitivity)
        if action == "block":
            return DlpResult(DlpVerdict.BLOCK, tuple(findings), "")
        if action == "redact":
            return DlpResult(
                DlpVerdict.REDACT, tuple(findings), self.redact(text, findings)
            )
        # audit: allow as-is but report the findings.
        return DlpResult(DlpVerdict.ALLOW, tuple(findings), text)
