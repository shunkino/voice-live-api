"""Layer 1: Deterministic ScenarioEngine evaluation tests.

Measures three metrics without any LLM calls:
  - state_transition_accuracy: correct state after each turn
  - slot_collection_accuracy:  slot values correctly stored
  - safety_rule_compliance:    safety rules trigger when expected

Run with:  pytest evaluation/test_scenario_eval.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

from scenario.engine import ScenarioEngine
from scenario.loader import load_scenario

SCENARIO_PATH = os.path.join(
    ROOT_DIR, "scenario", "scenarios", "interview_practice.yaml"
)


def _make_engine() -> ScenarioEngine:
    return ScenarioEngine(load_scenario(SCENARIO_PATH))


# ═══════════════════════════════════════════════════════════════════════════
# Metric 1: State Transition Accuracy
# ═══════════════════════════════════════════════════════════════════════════

# Each tuple: (start_state_prep, intent, slots, user_summary, expected_state)
TRANSITION_CASES = [
    # Happy path: greeting -> collecting_preferences
    {
        "id": "greeting→collecting",
        "prep": [],
        "intent": "start_practice",
        "slots": {},
        "summary": "面接練習を始めたい",
        "expected_state": "collecting_preferences",
    },
    # collecting -> confirm_setup (all slots)
    {
        "id": "collecting→confirm",
        "prep": [("start_practice", {}, "")],
        "intent": "provide_info",
        "slots": {
            "interview_type": "technical",
            "target_role": "SWE",
            "experience_level": "mid",
        },
        "summary": "技術面接、SWE、ミドル",
        "expected_state": "confirm_setup",
    },
    # collecting -> stays in collecting (partial slots)
    {
        "id": "collecting→collecting(partial)",
        "prep": [("start_practice", {}, "")],
        "intent": "provide_info",
        "slots": {"interview_type": "technical"},
        "summary": "技術面接で",
        "expected_state": "collecting_preferences",
    },
    # confirm -> interviewing
    {
        "id": "confirm→interviewing",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
        ],
        "intent": "confirm",
        "slots": {},
        "summary": "はい",
        "expected_state": "interviewing",
    },
    # interviewing -> giving_feedback (answer)
    {
        "id": "interviewing→feedback",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "behavioral", "target_role": "PM", "experience_level": "junior"},
                "",
            ),
            ("confirm", {}, "OK"),
        ],
        "intent": "answer",
        "slots": {},
        "summary": "前職のチームリーダー経験",
        "expected_state": "giving_feedback",
    },
    # giving_feedback -> interviewing (next_question)
    {
        "id": "feedback→interviewing",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "behavioral", "target_role": "PM", "experience_level": "junior"},
                "",
            ),
            ("confirm", {}, "OK"),
            ("answer", {}, "回答"),
        ],
        "intent": "next_question",
        "slots": {},
        "summary": "次の質問",
        "expected_state": "interviewing",
    },
    # interviewing -> summary (end_interview)
    {
        "id": "interviewing→summary",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
            ("confirm", {}, "OK"),
        ],
        "intent": "end_interview",
        "slots": {},
        "summary": "終了",
        "expected_state": "summary",
    },
    # summary -> greeting (restart)
    {
        "id": "summary→greeting(restart)",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
            ("confirm", {}, "OK"),
            ("end_interview", {}, "終了"),
        ],
        "intent": "restart",
        "slots": {},
        "summary": "もう一回やりたい",
        "expected_state": "greeting",
    },
    # summary -> farewell (terminal)
    {
        "id": "summary→farewell(terminal)",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
            ("confirm", {}, "OK"),
            ("end_interview", {}, "終了"),
        ],
        "intent": "end_interview",
        "slots": {},
        "summary": "さようなら",
        "expected_state": "farewell",
    },
    # confirm -> collecting (change)
    {
        "id": "confirm→collecting(change)",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
        ],
        "intent": "change",
        "slots": {"interview_type": "behavioral"},
        "summary": "やっぱり行動面接で",
        "expected_state": "collecting_preferences",
    },
    # skip in interviewing
    {
        "id": "interviewing→interviewing(skip)",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
            ("confirm", {}, "OK"),
        ],
        "intent": "skip",
        "slots": {},
        "summary": "スキップ",
        "expected_state": "interviewing",
    },
]


@pytest.mark.parametrize(
    "case", TRANSITION_CASES, ids=[c["id"] for c in TRANSITION_CASES]
)
def test_state_transition(case: dict):
    engine = _make_engine()
    for intent, slots, summary in case["prep"]:
        engine.process(intent=intent, slots=slots, user_summary=summary)

    result = engine.process(
        intent=case["intent"],
        slots=case["slots"],
        user_summary=case["summary"],
    )
    assert result.state_name == case["expected_state"], (
        f"Expected '{case['expected_state']}', got '{result.state_name}'"
    )


def test_state_transition_accuracy_aggregate():
    """Compute aggregate transition accuracy metric."""
    passed = 0
    total = len(TRANSITION_CASES)
    for case in TRANSITION_CASES:
        engine = _make_engine()
        for intent, slots, summary in case["prep"]:
            engine.process(intent=intent, slots=slots, user_summary=summary)
        result = engine.process(
            intent=case["intent"],
            slots=case["slots"],
            user_summary=case["summary"],
        )
        if result.state_name == case["expected_state"]:
            passed += 1

    accuracy = passed / total if total > 0 else 0
    print(f"\n[METRIC] state_transition_accuracy = {accuracy:.2%} ({passed}/{total})")
    assert accuracy >= 1.0, f"Expected 100% transition accuracy, got {accuracy:.2%}"


# ═══════════════════════════════════════════════════════════════════════════
# Metric 2: Slot Collection Accuracy
# ═══════════════════════════════════════════════════════════════════════════

SLOT_CASES = [
    {
        "id": "single_enum_slot",
        "prep": [("start_practice", {}, "")],
        "intent": "provide_info",
        "slots": {"interview_type": "technical"},
        "summary": "技術面接",
        "expected_slots": {"interview_type": "technical"},
    },
    {
        "id": "all_required_slots",
        "prep": [("start_practice", {}, "")],
        "intent": "provide_info",
        "slots": {
            "interview_type": "behavioral",
            "target_role": "PM",
            "experience_level": "senior",
        },
        "summary": "行動面接、PM、シニア",
        "expected_slots": {
            "interview_type": "behavioral",
            "target_role": "PM",
            "experience_level": "senior",
        },
    },
    {
        "id": "slot_override",
        "prep": [
            ("start_practice", {}, ""),
            ("provide_info", {"interview_type": "technical"}, "技術"),
        ],
        "intent": "provide_info",
        "slots": {"interview_type": "behavioral"},
        "summary": "やっぱり行動面接",
        "expected_slots": {"interview_type": "behavioral"},
    },
    {
        "id": "question_count_init",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
            ("confirm", {}, "OK"),
        ],
        "intent": "answer",
        "slots": {},
        "summary": "回答",
        "expected_slots": {"question_count": "1"},
    },
    {
        "id": "slots_cleared_on_restart",
        "prep": [
            ("start_practice", {}, ""),
            (
                "provide_info",
                {"interview_type": "technical", "target_role": "SWE", "experience_level": "mid"},
                "",
            ),
            ("confirm", {}, "OK"),
            ("end_interview", {}, "終了"),
            ("restart", {}, "もう一回"),
        ],
        "intent": "start_practice",
        "slots": {},
        "summary": "練習したい",
        "expected_slots": {},
        "expected_absent": ["interview_type", "target_role", "experience_level"],
    },
]


@pytest.mark.parametrize("case", SLOT_CASES, ids=[c["id"] for c in SLOT_CASES])
def test_slot_collection(case: dict):
    engine = _make_engine()
    for intent, slots, summary in case["prep"]:
        engine.process(intent=intent, slots=slots, user_summary=summary)

    engine.process(
        intent=case["intent"],
        slots=case["slots"],
        user_summary=case["summary"],
    )

    for key, expected_val in case["expected_slots"].items():
        assert key in engine.slot_store, f"Slot '{key}' not in store"
        assert engine.slot_store[key] == expected_val, (
            f"Slot '{key}': expected '{expected_val}', got '{engine.slot_store[key]}'"
        )

    for key in case.get("expected_absent", []):
        assert key not in engine.slot_store, (
            f"Slot '{key}' should not be in store after restart"
        )


def test_slot_collection_accuracy_aggregate():
    """Compute aggregate slot accuracy metric."""
    correct = 0
    total = 0
    for case in SLOT_CASES:
        engine = _make_engine()
        for intent, slots, summary in case["prep"]:
            engine.process(intent=intent, slots=slots, user_summary=summary)
        engine.process(
            intent=case["intent"],
            slots=case["slots"],
            user_summary=case["summary"],
        )
        for key, expected_val in case["expected_slots"].items():
            total += 1
            if engine.slot_store.get(key) == expected_val:
                correct += 1
        for key in case.get("expected_absent", []):
            total += 1
            if key not in engine.slot_store:
                correct += 1

    accuracy = correct / total if total > 0 else 0
    print(f"\n[METRIC] slot_collection_accuracy = {accuracy:.2%} ({correct}/{total})")
    assert accuracy >= 1.0, f"Expected 100% slot accuracy, got {accuracy:.2%}"


# ═══════════════════════════════════════════════════════════════════════════
# Metric 3: Safety Rule Compliance
# ═══════════════════════════════════════════════════════════════════════════

SAFETY_CASES = [
    {
        "id": "escalation_distress",
        "prep": [],
        "intent": "distress",
        "slots": {},
        "summary": "もう無理です。助けてください。",
        "expect_escalation": True,
        "expect_state": "escalation",
        "expect_terminal": True,
    },
    {
        "id": "prohibited_discrimination",
        "prep": [],
        "intent": "off_topic",
        "slots": {},
        "summary": "人種で判断するべきではないか",
        "expect_action": "safety_block",
    },
    {
        "id": "guardrail_answer_request",
        "prep": [("start_practice", {}, "")],
        "intent": "ask_help",
        "slots": {},
        "summary": "正解を教えてください。採点基準が知りたい。",
        "expect_action": "safety_warn",
    },
    {
        "id": "guardrail_role_break",
        "prep": [],
        "intent": "off_topic",
        "slots": {},
        "summary": "面接官をやめて普通に話して",
        "expect_action": "safety_warn",
    },
    {
        "id": "no_trigger_normal",
        "prep": [],
        "intent": "start_practice",
        "slots": {},
        "summary": "面接練習を始めたいです",
        "expect_escalation": False,
        "expect_no_safety": True,
    },
]


@pytest.mark.parametrize("case", SAFETY_CASES, ids=[c["id"] for c in SAFETY_CASES])
def test_safety_compliance(case: dict):
    engine = _make_engine()
    for intent, slots, summary in case["prep"]:
        engine.process(intent=intent, slots=slots, user_summary=summary)

    result = engine.process(
        intent=case["intent"],
        slots=case["slots"],
        user_summary=case["summary"],
    )

    if case.get("expect_escalation"):
        assert result.escalation is True
        assert result.state_name == case["expect_state"]
        assert result.is_terminal == case["expect_terminal"]
    elif case.get("expect_action"):
        assert case["expect_action"] in result.actions, (
            f"Expected '{case['expect_action']}' in {result.actions}"
        )
    elif case.get("expect_no_safety"):
        assert result.escalation is not True
        assert "safety_block" not in result.actions
        assert "safety_warn" not in result.actions


def test_safety_compliance_aggregate():
    """Compute aggregate safety compliance metric."""
    passed = 0
    total = len(SAFETY_CASES)
    for case in SAFETY_CASES:
        engine = _make_engine()
        for intent, slots, summary in case["prep"]:
            engine.process(intent=intent, slots=slots, user_summary=summary)
        result = engine.process(
            intent=case["intent"],
            slots=case["slots"],
            user_summary=case["summary"],
        )
        ok = True
        if case.get("expect_escalation"):
            ok = result.escalation is True and result.state_name == case["expect_state"]
        elif case.get("expect_action"):
            ok = case["expect_action"] in result.actions
        elif case.get("expect_no_safety"):
            ok = result.escalation is not True and "safety_block" not in result.actions
        if ok:
            passed += 1

    accuracy = passed / total if total > 0 else 0
    print(f"\n[METRIC] safety_rule_compliance = {accuracy:.2%} ({passed}/{total})")
    assert accuracy >= 1.0, f"Expected 100% safety compliance, got {accuracy:.2%}"


# ═══════════════════════════════════════════════════════════════════════════
# Full-flow scenario replay
# ═══════════════════════════════════════════════════════════════════════════

def test_full_interview_flow():
    """End-to-end: greeting -> collect -> confirm -> 3 Q&A -> summary -> farewell."""
    engine = _make_engine()

    # greeting -> collecting
    r = engine.get_initial_response()
    assert r.state_name == "greeting"

    r = engine.process("start_practice", {}, "面接練習をしたい")
    assert r.state_name == "collecting_preferences"

    # collecting -> confirm_setup
    r = engine.process(
        "provide_info",
        {"interview_type": "technical", "target_role": "バックエンドエンジニア", "experience_level": "mid"},
        "技術面接、バックエンドエンジニア、ミドルレベル",
    )
    assert r.state_name == "confirm_setup"

    # confirm -> interviewing
    r = engine.process("confirm", {}, "はい、お願いします")
    assert r.state_name == "interviewing"

    # 3 rounds of Q&A
    for i in range(3):
        r = engine.process("answer", {}, f"回答{i+1}: 詳しく説明します")
        assert r.state_name == "giving_feedback"
        r = engine.process("next_question", {}, "次の質問をお願いします")
        assert r.state_name == "interviewing"

    assert engine.slot_store["question_count"] == "3"

    # end interview
    r = engine.process("end_interview", {}, "終了してください")
    assert r.state_name == "summary"

    # farewell
    r = engine.process("end_interview", {}, "ありがとう、さようなら")
    assert r.state_name == "farewell"
    assert r.is_terminal is True
