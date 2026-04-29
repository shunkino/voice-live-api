"""Scenario State Machine engine."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from scenario.models import EngineResult, ScenarioConfig, Transition
from scenario.safety import SafetyEvaluator, SafetyResult

logger = logging.getLogger(__name__)


class ScenarioEngine:
    """State Machine that drives the conversation scenario.

    Manages current state, slot store, turn count, and evaluates
    transitions / safety rules on each user turn.
    """

    def __init__(self, config: ScenarioConfig):
        self.config = config
        self.current_state = config.initial_state
        self.slot_store: dict[str, str] = {}
        self.turn_count: int = 0
        self.history: list[dict[str, Any]] = []
        self._safety = SafetyEvaluator(config.safety_rules)

        # Initialise slots with defaults
        for name, sdef in config.slots.items():
            if sdef.default is not None:
                self.slot_store[name] = sdef.default

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_initial_response(self) -> EngineResult:
        """Return the greeting instructions for session start."""
        state = self.config.states[self.current_state]
        missing = self._missing_required_slots(state.name)
        return EngineResult(
            state_name=self.current_state,
            response_instructions=state.instructions.format_map(
                _SafeFormatDict(self.slot_store)
            ),
            collected_slots=dict(self.slot_store),
            missing_slots=missing,
        )

    def process(
        self,
        intent: str,
        slots: dict[str, str],
        user_summary: str,
    ) -> EngineResult:
        """Process one user turn through the state machine.

        1. Update slot store
        2. Safety check
        3. Evaluate transitions
        4. Generate response instructions
        """
        self.turn_count += 1

        # 1. Update slots
        self._update_slots(slots)

        # 2. Record history
        self.history.append(
            {
                "turn": self.turn_count,
                "state": self.current_state,
                "intent": intent,
                "slots": dict(slots),
                "user_summary": user_summary,
            }
        )

        logger.info(
            "Turn %d | state=%s | intent=%s | slots=%s",
            self.turn_count,
            self.current_state,
            intent,
            slots,
        )

        # 3. Safety evaluation
        safety: SafetyResult = self._safety.evaluate(
            user_summary=user_summary, intent=intent, slots=slots
        )
        if safety.triggered:
            return self._handle_safety(safety)

        # 4. Evaluate transitions
        state_cfg = self.config.states[self.current_state]
        matched_transition = self._find_transition(state_cfg.transitions, intent)

        actions: list[str] = []
        if matched_transition:
            actions = list(matched_transition.actions)
            self._execute_actions(actions)
            prev_state = self.current_state
            self.current_state = matched_transition.target_state
            logger.info("Transition: %s -> %s", prev_state, self.current_state)

        # 5. Build response instructions
        new_state_cfg = self.config.states[self.current_state]
        missing = self._missing_required_slots(self.current_state)
        instructions = new_state_cfg.instructions.format_map(
            _SafeFormatDict(self.slot_store)
        )

        # If there are missing required slots, append a hint
        if missing:
            slot_descs = []
            for s in missing:
                sdef = self.config.slots.get(s)
                desc = sdef.description if sdef else s
                slot_descs.append(f"- {s}: {desc}")
            instructions += (
                "\n\n以下の情報がまだ収集できていません。自然に聞いてください:\n"
                + "\n".join(slot_descs)
            )

        return EngineResult(
            state_name=self.current_state,
            response_instructions=instructions,
            collected_slots=dict(self.slot_store),
            missing_slots=missing,
            is_terminal=new_state_cfg.is_terminal,
            actions=actions,
        )

    def get_all_intents(self) -> list[str]:
        """Return union of all intents across all states (for tool enum)."""
        intents: set[str] = set()
        for state in self.config.states.values():
            intents.update(state.intents)
        return sorted(intents)

    def get_slot_properties(self) -> dict[str, Any]:
        """Return JSON Schema properties for slot extraction."""
        props: dict[str, Any] = {}
        for name, sdef in self.config.slots.items():
            if sdef.type == "int" or not sdef.required:
                # Internal-only slots or optional — skip from extraction
                continue
            prop: dict[str, Any] = {"type": "string", "description": sdef.description}
            if sdef.values:
                prop["enum"] = sdef.values
            props[name] = prop
        return props

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_slots(self, slots: dict[str, str]) -> None:
        """Merge new slot values into the store, validating enum types."""
        for name, value in slots.items():
            if not value:
                continue
            sdef = self.config.slots.get(name)
            if sdef is None:
                logger.warning("Unknown slot '%s' ignored", name)
                continue
            # Validate enum values
            if sdef.type == "enum" and sdef.values and value not in sdef.values:
                logger.warning(
                    "Slot '%s' value '%s' not in allowed values %s — storing anyway",
                    name, value, sdef.values,
                )
            self.slot_store[name] = value
            logger.info("Slot updated: %s = %s", name, value)

    def _missing_required_slots(self, state_name: str) -> list[str]:
        """Return list of required slots for a state that are not yet filled."""
        state_cfg = self.config.states.get(state_name)
        if not state_cfg:
            return []
        missing = []
        for slot_name in state_cfg.required_slots:
            if slot_name not in self.slot_store or not self.slot_store[slot_name]:
                missing.append(slot_name)
        return missing

    def _find_transition(
        self, transitions: list[Transition], intent: str
    ) -> Optional[Transition]:
        """Find the first matching transition given the current intent and state."""
        for t in transitions:
            # Intent match (if specified)
            if t.intent and t.intent != intent:
                continue

            # Condition match (if specified)
            if t.condition and not self._evaluate_condition(t.condition):
                continue

            # Required slots check (if specified)
            if t.required_slots:
                all_filled = all(
                    self.slot_store.get(s) for s in t.required_slots
                )
                if not all_filled:
                    continue

            return t
        return None

    def _evaluate_condition(self, condition: str) -> bool:
        """Evaluate a condition string against current state."""
        if condition == "all_required_slots_filled":
            return len(self._missing_required_slots(self.current_state)) == 0

        # Handle "slot >= N" style conditions
        if ">=" in condition:
            parts = condition.split(">=")
            if len(parts) == 2:
                slot_name = parts[0].strip()
                threshold = int(parts[1].strip())
                try:
                    return int(self.slot_store.get(slot_name, "0")) >= threshold
                except (ValueError, TypeError):
                    return False

        if ">" in condition:
            parts = condition.split(">")
            if len(parts) == 2:
                slot_name = parts[0].strip()
                threshold = int(parts[1].strip())
                try:
                    return int(self.slot_store.get(slot_name, "0")) > threshold
                except (ValueError, TypeError):
                    return False

        logger.warning("Unknown condition: %s", condition)
        return False

    def _execute_actions(self, actions: list[str]) -> None:
        """Execute side-effect actions from a transition."""
        for action in actions:
            if action == "reset_question_count":
                self.slot_store["question_count"] = "0"
                logger.info("Action: reset question_count")
            elif action == "increment_question_count":
                current = int(self.slot_store.get("question_count", "0"))
                self.slot_store["question_count"] = str(current + 1)
                logger.info("Action: increment question_count -> %d", current + 1)
            elif action == "clear_slots":
                # Keep only defaults
                self.slot_store.clear()
                for name, sdef in self.config.slots.items():
                    if sdef.default is not None:
                        self.slot_store[name] = sdef.default
                logger.info("Action: clear_slots")
            else:
                logger.warning("Unknown action: %s", action)

    def _handle_safety(self, safety: SafetyResult) -> EngineResult:
        """Handle a triggered safety rule."""
        logger.warning(
            "Safety rule triggered: type=%s action=%s",
            safety.rule_type,
            safety.action,
        )
        if safety.action == "escalate":
            self.current_state = "escalation"
            state_cfg = self.config.states.get("escalation")
            instructions = (
                state_cfg.instructions if state_cfg else safety.message or ""
            )
            return EngineResult(
                state_name="escalation",
                response_instructions=instructions,
                collected_slots=dict(self.slot_store),
                missing_slots=[],
                is_terminal=True,
                escalation=True,
                actions=["escalate"],
            )

        # block or warn — stay in current state
        state_cfg = self.config.states[self.current_state]
        instructions = (safety.message or "") + "\n\n" + state_cfg.instructions.format_map(
            _SafeFormatDict(self.slot_store)
        )
        return EngineResult(
            state_name=self.current_state,
            response_instructions=instructions,
            collected_slots=dict(self.slot_store),
            missing_slots=self._missing_required_slots(self.current_state),
            actions=["safety_" + (safety.action or "block")],
        )


class _SafeFormatDict(dict):
    """A dict subclass that returns '{key}' for missing keys in str.format_map()."""

    def __missing__(self, key: str) -> str:
        return f"(未設定)"
