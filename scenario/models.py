"""Data models for scenario-driven state machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SlotDefinition:
    """Definition of a slot that can be collected during conversation."""
    name: str
    type: str  # "str", "enum", "int"
    required: bool = False
    values: list[str] = field(default_factory=list)  # for enum type
    default: Optional[str] = None
    description: str = ""


@dataclass
class IntentDefinition:
    """Definition of an intent that can be recognized."""
    name: str
    description: str = ""
    example_utterances: list[str] = field(default_factory=list)


@dataclass
class Transition:
    """A state transition rule."""
    target_state: str
    intent: Optional[str] = None
    condition: Optional[str] = None
    required_slots: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


@dataclass
class StateConfig:
    """Configuration for a single state in the scenario."""
    name: str
    instructions: str  # LLM prompt for this state
    intents: list[str] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    required_slots: list[str] = field(default_factory=list)
    max_turns: Optional[int] = None
    is_terminal: bool = False


@dataclass
class SafetyRule:
    """A safety rule for conversation guardrails."""
    type: str  # "prohibited", "escalation", "guardrail"
    pattern: str  # keyword or pattern to match
    action: str  # "block", "escalate", "warn"
    message: str  # message to include in response instructions


@dataclass
class ScenarioConfig:
    """Top-level scenario configuration loaded from YAML."""
    name: str
    initial_state: str
    global_instructions: str
    slots: dict[str, SlotDefinition] = field(default_factory=dict)
    intents: dict[str, IntentDefinition] = field(default_factory=dict)
    states: dict[str, StateConfig] = field(default_factory=dict)
    safety_rules: list[SafetyRule] = field(default_factory=list)


@dataclass
class EngineResult:
    """Result returned by ScenarioEngine.process()."""
    state_name: str
    response_instructions: str
    collected_slots: dict[str, str] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    is_terminal: bool = False
    escalation: bool = False
    actions: list[str] = field(default_factory=list)
