"""Safety rule evaluator for scenario conversations."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from scenario.models import SafetyRule


@dataclass
class SafetyResult:
    """Result of safety evaluation."""
    triggered: bool = False
    action: Optional[str] = None  # "block", "escalate", "warn"
    message: Optional[str] = None
    rule_type: Optional[str] = None


class SafetyEvaluator:
    """Evaluates user input against safety rules."""

    def __init__(self, rules: list[SafetyRule]):
        self._rules = rules
        # Pre-compile regex patterns
        self._compiled: list[tuple[SafetyRule, re.Pattern[str]]] = []
        for rule in rules:
            pattern = re.compile(rule.pattern, re.IGNORECASE)
            self._compiled.append((rule, pattern))

    def evaluate(
        self,
        user_summary: str,
        intent: str = "",
        slots: Optional[dict[str, str]] = None,
    ) -> SafetyResult:
        """Evaluate user input against all safety rules.

        Checks are ordered by severity: escalation > prohibited > guardrail.
        Returns the first matching rule.
        """
        text = f"{intent} {user_summary}"
        if slots:
            text += " " + " ".join(str(v) for v in slots.values())

        # Check escalation rules first (highest priority)
        for rule, pattern in self._compiled:
            if rule.type == "escalation" and pattern.search(text):
                return SafetyResult(
                    triggered=True,
                    action=rule.action,
                    message=rule.message,
                    rule_type=rule.type,
                )

        # Then prohibited
        for rule, pattern in self._compiled:
            if rule.type == "prohibited" and pattern.search(text):
                return SafetyResult(
                    triggered=True,
                    action=rule.action,
                    message=rule.message,
                    rule_type=rule.type,
                )

        # Then guardrails
        for rule, pattern in self._compiled:
            if rule.type == "guardrail" and pattern.search(text):
                return SafetyResult(
                    triggered=True,
                    action=rule.action,
                    message=rule.message,
                    rule_type=rule.type,
                )

        return SafetyResult()
