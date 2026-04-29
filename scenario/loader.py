"""YAML scenario loader with validation."""
from __future__ import annotations

import os
from typing import Any

import yaml

from scenario.models import (
    IntentDefinition,
    SafetyRule,
    ScenarioConfig,
    SlotDefinition,
    StateConfig,
    Transition,
)


def load_scenario(path: str) -> ScenarioConfig:
    """Load a scenario from a YAML file and validate it.

    Args:
        path: Path to the YAML scenario file.

    Returns:
        A validated ScenarioConfig.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If the scenario definition is invalid.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Scenario file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    # --- Parse slots ---
    slots: dict[str, SlotDefinition] = {}
    for name, sdef in raw.get("slots", {}).items():
        slots[name] = SlotDefinition(
            name=name,
            type=sdef.get("type", "str"),
            required=sdef.get("required", False),
            values=sdef.get("values", []),
            default=sdef.get("default"),
            description=sdef.get("description", ""),
        )

    # --- Parse intents ---
    intents: dict[str, IntentDefinition] = {}
    for name, idef in raw.get("intents", {}).items():
        intents[name] = IntentDefinition(
            name=name,
            description=idef.get("description", ""),
            example_utterances=idef.get("example_utterances", []),
        )

    # --- Parse states ---
    states: dict[str, StateConfig] = {}
    for name, sdef in raw.get("states", {}).items():
        transitions: list[Transition] = []
        for tdef in sdef.get("transitions", []):
            transitions.append(
                Transition(
                    target_state=tdef["target_state"],
                    intent=tdef.get("intent"),
                    condition=tdef.get("condition"),
                    required_slots=tdef.get("required_slots", []),
                    actions=tdef.get("actions", []),
                )
            )
        states[name] = StateConfig(
            name=name,
            instructions=sdef.get("instructions", ""),
            intents=sdef.get("intents", []),
            transitions=transitions,
            required_slots=sdef.get("required_slots", []),
            max_turns=sdef.get("max_turns"),
            is_terminal=sdef.get("is_terminal", False),
        )

    # --- Parse safety rules ---
    safety_rules: list[SafetyRule] = []
    for rdef in raw.get("safety_rules", []):
        safety_rules.append(
            SafetyRule(
                type=rdef["type"],
                pattern=rdef["pattern"],
                action=rdef["action"],
                message=rdef["message"],
            )
        )

    config = ScenarioConfig(
        name=raw["name"],
        initial_state=raw["initial_state"],
        global_instructions=raw.get("global_instructions", ""),
        slots=slots,
        intents=intents,
        states=states,
        safety_rules=safety_rules,
    )

    _validate(config)
    return config


def _validate(config: ScenarioConfig) -> None:
    """Validate a scenario config for consistency."""
    errors: list[str] = []

    # initial_state must exist
    if config.initial_state not in config.states:
        errors.append(f"initial_state '{config.initial_state}' not found in states")

    # All transition target_states must exist
    for state_name, state in config.states.items():
        for i, t in enumerate(state.transitions):
            if t.target_state not in config.states:
                errors.append(
                    f"State '{state_name}' transition[{i}] target_state "
                    f"'{t.target_state}' not found in states"
                )

        # All required_slots in states must be defined
        for slot_name in state.required_slots:
            if slot_name not in config.slots:
                errors.append(
                    f"State '{state_name}' required_slot "
                    f"'{slot_name}' not defined in slots"
                )

        # All intents in states must be defined
        for intent_name in state.intents:
            if intent_name not in config.intents:
                errors.append(
                    f"State '{state_name}' intent "
                    f"'{intent_name}' not defined in intents"
                )

    if errors:
        raise ValueError(
            "Scenario validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )
