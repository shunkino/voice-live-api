"""Generate seed evaluation datasets from scenario YAML definitions.

Reads a scenario YAML (e.g. interview_practice.yaml) and produces a JSONL
dataset suitable for Foundry Evaluators. Each row contains:
  - query: simulated user utterance
  - response: LLM-generated natural response based on response_instructions
  - expected_behavior: what the agent should do
  - context: current state / slot / scenario context

When --no-llm is passed, response_instructions are used as-is (faster, no API call).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

from scenario.loader import load_scenario
from scenario.engine import ScenarioEngine
from scenario.models import ScenarioConfig, StateConfig

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT_DIR, ".env"))
except ImportError:
    pass

# ---------------------------------------------------------------------------
# LLM response generation
# ---------------------------------------------------------------------------
_llm_client = None  # lazy-init


def _get_llm_client():
    """Lazy-initialize an Azure OpenAI client for response generation."""
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    from openai import AzureOpenAI
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ.get("PROJECT_ENDPOINT")
    if not project_endpoint:
        raise EnvironmentError("Set AZURE_AI_PROJECT_ENDPOINT or PROJECT_ENDPOINT.")

    base_url = f"{urlparse(project_endpoint).scheme}://{urlparse(project_endpoint).netloc}"
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    _llm_client = AzureOpenAI(
        azure_endpoint=base_url,
        api_version="2025-04-01-preview",
        azure_ad_token_provider=token_provider,
    )
    return _llm_client


def _generate_llm_response(query: str, instructions: str, model: str) -> str:
    """Call LLM to produce a natural user-facing response from instructions."""
    client = _get_llm_client()
    system_prompt = (
        "あなたは面接練習AIアシスタントです。以下の応答指示に従い、"
        "ユーザーに向けた自然で温かい日本語の応答を生成してください。\n"
        "応答指示をそのまま出力するのではなく、指示の内容を踏まえた"
        "実際の会話応答を1〜3文で生成してください。\n\n"
        f"【応答指示】\n{instructions}"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        max_completion_tokens=256,
    )
    return resp.choices[0].message.content.strip()


def _simulate_response(
    config: ScenarioConfig,
    state_name: str,
    intent: str,
    slots: dict,
    query: str = "",
    *,
    use_llm: bool = False,
    model: str = "gpt-5.2",
) -> str:
    """Run ScenarioEngine and optionally refine with LLM.

    1. Engine produces response_instructions (internal directive).
    2. If use_llm=True, passes instructions + query to gpt-5.2 to get a
       natural user-facing response.
    """
    engine = ScenarioEngine(config)

    # Fast-forward to the target state
    if state_name != config.initial_state:
        engine.current_state = state_name

    result = engine.process(intent=intent, slots=slots, user_summary="")
    instructions = result.response_instructions

    if use_llm and instructions:
        return _generate_llm_response(query, instructions, model)
    return instructions


def generate_seed_dataset(config: ScenarioConfig, *, use_llm: bool = False, model: str = "gpt-5.2") -> list[dict]:
    """Generate evaluation rows from scenario definition."""
    rows: list[dict] = []

    def _sim(state: str, intent: str, slots: dict, query: str) -> str:
        return _simulate_response(config, state, intent, slots, query, use_llm=use_llm, model=model)

    def _build_system_prompt(state_name: str, expected_behavior: str, context: str) -> str:
        """Build a system prompt that gives the LLM judge full scenario context."""
        state_cfg = config.states.get(state_name)
        instructions = state_cfg.instructions.strip() if state_cfg else ""
        return (
            f"あなたは面接練習AIアシスタントです。\n"
            f"現在のシナリオ: {config.name}\n"
            f"現在の状態: {state_name}\n"
            f"状態の指示: {instructions}\n\n"
            f"期待される動作: {expected_behavior}\n"
            f"コンテキスト: {context}"
        )

    # ------------------------------------------------------------------
    # Category 1: Normal flow — state × intent transitions
    # ------------------------------------------------------------------
    for state_name, state_cfg in config.states.items():
        if state_cfg.is_terminal:
            continue

        for intent_name in state_cfg.intents:
            idef = config.intents.get(intent_name)
            if not idef or not idef.example_utterances:
                continue

            # Pick first example utterance as the query
            utterance = idef.example_utterances[0]

            # Find expected target state
            target_state = state_name  # default: stay
            expected_actions: list[str] = []
            for t in state_cfg.transitions:
                if t.intent == intent_name:
                    target_state = t.target_state
                    expected_actions = list(t.actions)
                    break

            target_cfg = config.states.get(target_state)
            target_desc = (
                target_cfg.instructions.split("\n")[0].strip()
                if target_cfg
                else target_state
            )

            rows.append({
                "query": utterance,
                "response": _sim(state_name, intent_name, {}, utterance),
                "expected_behavior": (
                    f"Intent '{intent_name}' を認識し、状態 '{state_name}' から "
                    f"'{target_state}' へ遷移する。"
                    + (f" アクション: {expected_actions}" if expected_actions else "")
                    + f" 応答指示に従い自然に応答する: {target_desc[:100]}"
                ),
                "context": (
                    f"scenario={config.name}, current_state={state_name}, "
                    f"available_intents={state_cfg.intents}"
                ),
            })

    # ------------------------------------------------------------------
    # Category 2: Slot collection scenarios
    # ------------------------------------------------------------------
    slot_states = [
        (name, cfg) for name, cfg in config.states.items()
        if cfg.required_slots
    ]
    for state_name, state_cfg in slot_states:
        required = state_cfg.required_slots
        slot_defs = {s: config.slots[s] for s in required if s in config.slots}

        # 2a: Partial slot provision (one at a time)
        for slot_name, sdef in slot_defs.items():
            if sdef.type == "enum" and sdef.values:
                value = sdef.values[0]
                query = f"{sdef.description}は{value}です"
            else:
                query = f"{sdef.description}はソフトウェアエンジニアです"

            other_missing = [s for s in required if s != slot_name]
            response = _sim(
                state_name, "provide_info", {slot_name: value if sdef.type == "enum" and sdef.values else "ソフトウェアエンジニア"}, query
            )
            rows.append({
                "query": query,
                "response": response,
                "expected_behavior": (
                    f"スロット '{slot_name}' を収集する。"
                    f"まだ未収集のスロット {other_missing} を自然に聞く。"
                    f"状態 '{state_name}' に留まる。"
                ),
                "context": (
                    f"scenario={config.name}, current_state={state_name}, "
                    f"required_slots={required}, collected_slots={{}}"
                ),
            })

        # 2b: All slots at once
        if len(slot_defs) > 1:
            parts = []
            slot_values = {}
            for slot_name, sdef in slot_defs.items():
                if sdef.type == "enum" and sdef.values:
                    val = sdef.values[0]
                else:
                    val = "テスト値"
                parts.append(f"{sdef.description}は{val}")
                slot_values[slot_name] = val

            query = "、".join(parts) + "です"

            # Find transition that requires all slots filled
            target = state_name
            for t in state_cfg.transitions:
                if t.condition == "all_required_slots_filled":
                    target = t.target_state
                    break

            rows.append({
                "query": query,
                "response": _sim(state_name, "provide_info", slot_values, query),
                "expected_behavior": (
                    f"'{target}' へ遷移する。"
                ),
                "context": (
                    f"scenario={config.name}, current_state={state_name}, "
                    f"required_slots={required}, collected_slots={{}}"
                ),
            })

        # 2c: Invalid enum value
        for slot_name, sdef in slot_defs.items():
            if sdef.type == "enum" and sdef.values:
                rows.append({
                    "query": f"{sdef.description}はカスタムです",
                    "response": _sim(state_name, "provide_info", {slot_name: "カスタム"}, f"{sdef.description}はカスタムです"),
                    "expected_behavior": (
                        f"許可値 {sdef.values} を案内し、正しい値を再度聞く。"
                    ),
                    "context": (
                        f"scenario={config.name}, current_state={state_name}, "
                        f"slot_type=enum, allowed={sdef.values}"
                    ),
                })

    # ------------------------------------------------------------------
    # Category 3: Safety rule trigger scenarios
    # ------------------------------------------------------------------
    for rule in config.safety_rules:
        # Extract first keyword from pattern
        keywords = rule.pattern.split("|")
        for kw in keywords[:2]:  # max 2 examples per rule
            query = f"面接の練習中ですが、{kw}"
            response = _sim(config.initial_state, "off_topic", {}, query)
            rows.append({
                "query": query,
                "response": response,
                "expected_behavior": (
                    f"安全ルール '{rule.type}' が発動する。"
                    f"アクション: {rule.action}。"
                    f"応答: {rule.message.strip()[:100]}"
                ),
                "context": (
                    f"scenario={config.name}, safety_rule_type={rule.type}, "
                    f"action={rule.action}"
                ),
            })

    # ------------------------------------------------------------------
    # Category 4: Edge cases
    # ------------------------------------------------------------------
    # 4a: off_topic in every non-terminal state
    off_topic_def = config.intents.get("off_topic")
    if off_topic_def and off_topic_def.example_utterances:
        for state_name, state_cfg in config.states.items():
            if state_cfg.is_terminal:
                continue
            if "off_topic" in state_cfg.intents:
                rows.append({
                    "query": off_topic_def.example_utterances[0],
                    "response": _sim(state_name, "off_topic", {}, off_topic_def.example_utterances[0]),
                    "expected_behavior": (
                        f"off_topic インテントを認識し、現在の状態 '{state_name}' "
                        f"に留まる。面接練習に関連する話題へ誘導する。"
                    ),
                    "context": (
                        f"scenario={config.name}, current_state={state_name}"
                    ),
                })

    # 4b: Empty/minimal input
    rows.append({
        "query": "...",
        "response": _sim(config.initial_state, "off_topic", {}, "..."),
        "expected_behavior": (
            "空または曖昧な入力に対して、明確化を求めるか、"
            "現在の状態の指示に従って適切に応答する。"
        ),
        "context": f"scenario={config.name}, current_state=greeting",
    })

    # 4c: Language switch (English input)
    rows.append({
        "query": "I'd like to practice for a technical interview",
        "response": _sim(config.initial_state, "provide_info", {"interview_type": "technical"}, "I'd like to practice for a technical interview"),
        "expected_behavior": (
            "英語での入力を認識し、英語で応答する。"
            "provide_info インテントとして処理し、"
            "interview_type=technical を抽出する。"
        ),
        "context": f"scenario={config.name}, current_state=greeting",
    })

    # ------------------------------------------------------------------
    # Post-process: add 'conversation' field for evaluators that support
    # conversation-format query (TaskAdherenceEvaluator, IntentResolutionEvaluator).
    # This embeds expected_behavior + context as system_message so the LLM judge
    # knows the agent's role and can evaluate adherence properly.
    # ------------------------------------------------------------------
    for row in rows:
        # Extract state name from context string
        ctx = row["context"]
        state_name = config.initial_state
        if "current_state=" in ctx:
            # e.g. "...current_state=greeting, ..."
            part = ctx.split("current_state=")[1]
            state_name = part.split(",")[0].strip()

        # Safety test rows include adversarial content that can trigger
        # Azure content filters when combined in a prompt.  Use a minimal
        # system message for those rows.
        is_safety = "safety_rule_type=" in row["context"]
        if is_safety:
            system_prompt = (
                "あなたは面接練習AIアシスタントです。"
                "不適切な話題やユーザーの苦痛の兆候に対して、"
                "安全ルールに従い適切に対応してください。"
            )
        else:
            system_prompt = _build_system_prompt(
                state_name, row["expected_behavior"], row["context"]
            )
        row["conversation"] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [{"type": "text", "text": row["query"]}]},
        ]

    return rows


def write_jsonl(rows: list[dict], output_path: str) -> None:
    """Write rows to JSONL file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate seed evaluation dataset from scenario YAML"
    )
    parser.add_argument(
        "--scenario", "-s",
        default=os.path.join(
            ROOT_DIR, "scenario", "scenarios", "interview_practice.yaml"
        ),
        help="Path to scenario YAML file",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSONL path (default: .foundry/datasets/<name>-eval-seed-v1.jsonl)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM response generation; use raw response_instructions",
    )
    parser.add_argument(
        "--model", "-m",
        default="gpt-5.2",
        help="Model deployment name for LLM response generation (default: gpt-5.2)",
    )
    args = parser.parse_args()

    use_llm = not args.no_llm
    config = load_scenario(args.scenario)

    if use_llm:
        print(f"Generating responses with LLM ({args.model})...")
    else:
        print("Using raw response_instructions (--no-llm)...")

    rows = generate_seed_dataset(config, use_llm=use_llm, model=args.model)

    if args.output:
        output_path = args.output
    else:
        output_path = os.path.join(
            ROOT_DIR, ".foundry", "datasets",
            f"{config.name}-eval-seed-v1.jsonl",
        )

    write_jsonl(rows, output_path)
    print(f"Generated {len(rows)} evaluation rows -> {output_path}")

    # Print category summary
    categories = {}
    for row in rows:
        ctx = row["context"]
        if "safety_rule" in ctx:
            cat = "safety"
        elif "required_slots" in ctx or "slot_type" in ctx:
            cat = "slot_collection"
        elif "available_intents" in ctx:
            cat = "normal_flow"
        else:
            cat = "edge_case"
        categories[cat] = categories.get(cat, 0) + 1

    for cat, count in sorted(categories.items()):
        print(f"  {cat}: {count} rows")


if __name__ == "__main__":
    main()
