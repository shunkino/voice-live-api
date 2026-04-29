"""Unit tests for ScenarioEngine and YAML loader."""
from __future__ import annotations

import os
import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Ensure the project root is importable
import sys
sys.path.insert(0, ROOT_DIR)

from scenario.loader import load_scenario
from scenario.engine import ScenarioEngine
from scenario.safety import SafetyEvaluator, SafetyResult
from scenario.models import SafetyRule

SCENARIO_PATH = os.path.join(ROOT_DIR, "scenario", "scenarios", "interview_practice.yaml")


# ═══════════════════════════════════════════════════════════════════════════
# YAML Loader Tests
# ═══════════════════════════════════════════════════════════════════════════
class TestLoader:
    def test_load_scenario(self):
        config = load_scenario(SCENARIO_PATH)
        assert config.name == "interview_practice"
        assert config.initial_state == "greeting"
        assert "greeting" in config.states
        assert "interviewing" in config.states
        assert "farewell" in config.states
        assert "escalation" in config.states
        assert "interview_type" in config.slots
        assert config.slots["interview_type"].required is True

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_scenario("/nonexistent/file.yaml")

    def test_all_transitions_valid(self):
        config = load_scenario(SCENARIO_PATH)
        for state_name, state in config.states.items():
            for t in state.transitions:
                assert t.target_state in config.states, (
                    f"State '{state_name}' has invalid target '{t.target_state}'"
                )

    def test_all_intents_defined(self):
        config = load_scenario(SCENARIO_PATH)
        for state_name, state in config.states.items():
            for intent in state.intents:
                assert intent in config.intents, (
                    f"State '{state_name}' references undefined intent '{intent}'"
                )

    def test_all_required_slots_defined(self):
        config = load_scenario(SCENARIO_PATH)
        for state_name, state in config.states.items():
            for slot in state.required_slots:
                assert slot in config.slots, (
                    f"State '{state_name}' requires undefined slot '{slot}'"
                )


# ═══════════════════════════════════════════════════════════════════════════
# ScenarioEngine Tests
# ═══════════════════════════════════════════════════════════════════════════
class TestEngine:
    def _make_engine(self) -> ScenarioEngine:
        config = load_scenario(SCENARIO_PATH)
        return ScenarioEngine(config)

    def test_initial_state(self):
        engine = self._make_engine()
        assert engine.current_state == "greeting"

    def test_initial_response(self):
        engine = self._make_engine()
        result = engine.get_initial_response()
        assert result.state_name == "greeting"
        assert len(result.response_instructions) > 0

    def test_greeting_to_collecting(self):
        engine = self._make_engine()
        result = engine.process(
            intent="start_practice", slots={}, user_summary="面接練習を始めたい"
        )
        assert result.state_name == "collecting_preferences"

    def test_provide_info_with_slots(self):
        engine = self._make_engine()
        # Go to collecting_preferences first
        engine.process(intent="start_practice", slots={}, user_summary="練習したい")
        assert engine.current_state == "collecting_preferences"

        # Provide partial info
        result = engine.process(
            intent="provide_info",
            slots={"interview_type": "technical"},
            user_summary="技術面接でお願いします",
        )
        # Still in collecting (missing slots)
        assert result.state_name == "collecting_preferences"
        assert "target_role" in result.missing_slots
        assert "experience_level" in result.missing_slots

    def test_full_slot_collection_to_confirm(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="練習したい")

        # Provide all slots at once
        result = engine.process(
            intent="provide_info",
            slots={
                "interview_type": "behavioral",
                "target_role": "プロジェクトマネージャー",
                "experience_level": "senior",
            },
            user_summary="行動面接、PM志望、シニアです",
        )
        assert result.state_name == "confirm_setup"
        assert len(result.missing_slots) == 0

    def test_confirm_to_interviewing(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="練習したい")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "technical",
                "target_role": "SWE",
                "experience_level": "mid",
            },
            user_summary="技術面接、SWE、ミドル",
        )
        assert engine.current_state == "confirm_setup"

        result = engine.process(
            intent="confirm", slots={}, user_summary="はい、お願いします"
        )
        assert result.state_name == "interviewing"
        assert engine.slot_store["question_count"] == "0"

    def test_answer_to_feedback(self):
        engine = self._make_engine()
        # Fast-forward to interviewing
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "behavioral",
                "target_role": "PM",
                "experience_level": "junior",
            },
            user_summary="",
        )
        engine.process(intent="confirm", slots={}, user_summary="OK")
        assert engine.current_state == "interviewing"

        result = engine.process(
            intent="answer", slots={}, user_summary="前職ではチームリーダーとして..."
        )
        assert result.state_name == "giving_feedback"
        assert engine.slot_store["question_count"] == "1"

    def test_feedback_to_next_question(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "behavioral",
                "target_role": "PM",
                "experience_level": "junior",
            },
            user_summary="",
        )
        engine.process(intent="confirm", slots={}, user_summary="OK")
        engine.process(intent="answer", slots={}, user_summary="回答")
        assert engine.current_state == "giving_feedback"

        result = engine.process(
            intent="next_question", slots={}, user_summary="次の質問を"
        )
        assert result.state_name == "interviewing"

    def test_end_interview_to_summary(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "technical",
                "target_role": "SWE",
                "experience_level": "mid",
            },
            user_summary="",
        )
        engine.process(intent="confirm", slots={}, user_summary="OK")
        assert engine.current_state == "interviewing"

        result = engine.process(
            intent="end_interview", slots={}, user_summary="終わりにしたい"
        )
        assert result.state_name == "summary"

    def test_summary_restart(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "technical",
                "target_role": "SWE",
                "experience_level": "mid",
            },
            user_summary="",
        )
        engine.process(intent="confirm", slots={}, user_summary="OK")
        engine.process(intent="end_interview", slots={}, user_summary="終了")
        assert engine.current_state == "summary"

        result = engine.process(
            intent="restart", slots={}, user_summary="もう一回やりたい"
        )
        assert result.state_name == "greeting"
        # Slots should be cleared (except defaults)
        assert "interview_type" not in engine.slot_store

    def test_farewell_is_terminal(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "technical",
                "target_role": "SWE",
                "experience_level": "mid",
            },
            user_summary="",
        )
        engine.process(intent="confirm", slots={}, user_summary="OK")
        engine.process(intent="end_interview", slots={}, user_summary="終了")
        result = engine.process(
            intent="end_interview", slots={}, user_summary="さようなら"
        )
        assert result.state_name == "farewell"
        assert result.is_terminal is True

    def test_question_count_limit(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "behavioral",
                "target_role": "PM",
                "experience_level": "mid",
            },
            user_summary="",
        )
        engine.process(intent="confirm", slots={}, user_summary="OK")
        assert engine.current_state == "interviewing"

        # Answer 5 questions
        for i in range(5):
            engine.process(intent="answer", slots={}, user_summary=f"回答{i}")
            if engine.current_state == "giving_feedback":
                engine.process(intent="next_question", slots={}, user_summary="次")

        # After 5 questions, the condition question_count >= 5 should trigger
        # summary transition on next turn
        assert int(engine.slot_store.get("question_count", "0")) >= 5

    def test_skip_increments_question_count(self):
        engine = self._make_engine()
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "technical",
                "target_role": "SWE",
                "experience_level": "mid",
            },
            user_summary="",
        )
        engine.process(intent="confirm", slots={}, user_summary="OK")

        result = engine.process(intent="skip", slots={}, user_summary="スキップ")
        assert result.state_name == "interviewing"
        assert engine.slot_store["question_count"] == "1"

    def test_get_all_intents(self):
        engine = self._make_engine()
        intents = engine.get_all_intents()
        assert "start_practice" in intents
        assert "provide_info" in intents
        assert "answer" in intents
        assert "distress" in intents

    def test_get_slot_properties(self):
        engine = self._make_engine()
        props = engine.get_slot_properties()
        assert "interview_type" in props
        assert "target_role" in props
        assert "experience_level" in props
        # question_count should not be exposed (it's int / non-required)
        assert "question_count" not in props


# ═══════════════════════════════════════════════════════════════════════════
# Safety Evaluator Tests
# ═══════════════════════════════════════════════════════════════════════════
class TestSafety:
    def test_escalation_on_distress(self):
        engine = ScenarioEngine(load_scenario(SCENARIO_PATH))
        result = engine.process(
            intent="distress", slots={}, user_summary="もう無理です。助けてください。"
        )
        assert result.escalation is True
        assert result.state_name == "escalation"
        assert result.is_terminal is True

    def test_guardrail_on_answer_request(self):
        engine = ScenarioEngine(load_scenario(SCENARIO_PATH))
        engine.process(intent="start_practice", slots={}, user_summary="")
        result = engine.process(
            intent="ask_help",
            slots={},
            user_summary="正解を教えてください。採点基準が知りたい。",
        )
        # Should stay in current state with a warning
        assert result.state_name == "collecting_preferences"
        assert "safety_warn" in result.actions

    def test_prohibited_topic(self):
        engine = ScenarioEngine(load_scenario(SCENARIO_PATH))
        result = engine.process(
            intent="off_topic",
            slots={},
            user_summary="人種で判断するべきではないか",
        )
        # Should block but stay in current state
        assert "safety_block" in result.actions

    def test_no_safety_trigger_on_normal(self):
        evaluator = SafetyEvaluator([
            SafetyRule(
                type="escalation",
                pattern="助けて",
                action="escalate",
                message="test",
            )
        ])
        result = evaluator.evaluate(user_summary="面接練習を始めたいです")
        assert result.triggered is False


# ═══════════════════════════════════════════════════════════════════════════
# Change setup transition test
# ═══════════════════════════════════════════════════════════════════════════
class TestChangeFlow:
    def test_change_goes_back_to_collecting(self):
        engine = ScenarioEngine(load_scenario(SCENARIO_PATH))
        engine.process(intent="start_practice", slots={}, user_summary="")
        engine.process(
            intent="provide_info",
            slots={
                "interview_type": "technical",
                "target_role": "SWE",
                "experience_level": "mid",
            },
            user_summary="",
        )
        assert engine.current_state == "confirm_setup"

        result = engine.process(
            intent="change",
            slots={"interview_type": "behavioral"},
            user_summary="やっぱり行動面接で",
        )
        assert result.state_name == "collecting_preferences"
        assert engine.slot_store["interview_type"] == "behavioral"
