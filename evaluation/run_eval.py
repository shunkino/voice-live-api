"""Layer 2: Foundry SDK local evaluation with gpt-5.2 LLM judge.

Runs Phase 1 built-in evaluators from azure-ai-evaluation SDK:
  - RelevanceEvaluator:       応答のユーザー質問への関連性
  - TaskAdherenceEvaluator:   response_instructions への準拠度
  - IntentResolutionEvaluator: ユーザーインテント解決度
  - IndirectAttackEvaluator:  間接的な攻撃の検出
  - ToolCallAccuracyEvaluator: classify_and_act の呼び出し精度

Usage:
  python evaluation/run_eval.py --dataset .foundry/datasets/interview_practice-eval-seed-v1.jsonl
  python evaluation/run_eval.py --dataset .foundry/datasets/interview_practice-eval-logs-v1.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT_DIR, ".env"))
except ImportError:
    pass

from azure.ai.evaluation import (
    EvaluatorConfig,
    evaluate,
    IndirectAttackEvaluator,
    IntentResolutionEvaluator,
    RelevanceEvaluator,
    TaskAdherenceEvaluator,
    ToolCallAccuracyEvaluator,
)
from azure.identity import DefaultAzureCredential


def _get_project_endpoint() -> str:
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ.get(
        "PROJECT_ENDPOINT"
    )
    if not endpoint:
        raise EnvironmentError(
            "Set AZURE_AI_PROJECT_ENDPOINT or PROJECT_ENDPOINT environment variable."
        )
    return endpoint


def _get_model_deployment() -> str:
    return os.environ.get("AZURE_EVALUATION_MODEL_DEPLOYMENT", "gpt-5.2")


def run_evaluation(
    dataset_path: str,
    output_dir: str | None = None,
    evaluator_names: list[str] | None = None,
) -> dict:
    """Run Foundry built-in evaluators against a JSONL dataset.

    Args:
        dataset_path: Path to evaluation JSONL.
        output_dir:   Directory for result output. Defaults to .foundry/results/.
        evaluator_names: Which evaluators to run. None = all Phase 1 evaluators.

    Returns:
        Evaluation result dict from azure-ai-evaluation SDK.
    """
    project_endpoint = _get_project_endpoint()
    model_deployment = _get_model_deployment()
    credential = DefaultAzureCredential()

    # Derive the base Azure AI Services endpoint from the Foundry project endpoint.
    # PROJECT_ENDPOINT is like https://xxx.services.ai.azure.com/api/projects/yyy
    # but model_config needs just the base: https://xxx.services.ai.azure.com
    from urllib.parse import urlparse
    parsed = urlparse(project_endpoint)
    azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"

    # Shared model_config for LLM judge evaluators
    model_config = {
        "azure_endpoint": azure_endpoint,
        "azure_deployment": model_deployment,
        "api_version": "2025-04-01-preview",
    }

    # Build evaluator instances and column mappings separately.
    # evaluate() takes `evaluators` (instances) and `evaluator_config` (EvaluatorConfig with column_mapping).
    # is_reasoning_model=True tells the SDK to use max_completion_tokens instead of
    # max_tokens, which is required by newer models like gpt-5.2.
    all_instances: dict[str, object] = {
        "relevance": RelevanceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True),
        "task_adherence": TaskAdherenceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True),
        "intent_resolution": IntentResolutionEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True),
        "indirect_attack": IndirectAttackEvaluator(credential=credential, azure_ai_project=project_endpoint),
        "tool_call_accuracy": ToolCallAccuracyEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True),
    }

    all_configs: dict[str, EvaluatorConfig] = {
        "relevance": EvaluatorConfig(
            column_mapping={
                "query": "${data.query}",
                "response": "${data.response}",
            },
        ),
        "task_adherence": EvaluatorConfig(
            column_mapping={
                "query": "${data.conversation}",
                "response": "${data.response}",
            },
        ),
        "intent_resolution": EvaluatorConfig(
            column_mapping={
                "query": "${data.conversation}",
                "response": "${data.response}",
            },
        ),
        "indirect_attack": EvaluatorConfig(
            column_mapping={
                "query": "${data.query}",
                "response": "${data.response}",
            },
        ),
        "tool_call_accuracy": EvaluatorConfig(
            column_mapping={
                "query": "${data.query}",
                "response": "${data.response}",
                "tool_definitions": "${data.context}",
            },
        ),
    }

    # Filter evaluators if specified
    if evaluator_names:
        selected_instances = {k: v for k, v in all_instances.items() if k in evaluator_names}
        selected_configs = {k: v for k, v in all_configs.items() if k in evaluator_names}
    else:
        selected_instances = all_instances
        selected_configs = all_configs

    if not selected_instances:
        raise ValueError(
            f"No valid evaluators selected. Available: {list(all_instances.keys())}"
        )

    print(f"Running evaluators: {list(selected_instances.keys())}")
    print(f"Dataset: {dataset_path}")
    print(f"Model deployment: {model_deployment}")

    # Run evaluation
    result = evaluate(
        data=dataset_path,
        evaluators=selected_instances,
        evaluator_config=selected_configs,
        azure_ai_project=project_endpoint,
    )

    # Save results
    if output_dir is None:
        output_dir = os.path.join(ROOT_DIR, ".foundry", "results")
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = os.path.join(output_dir, f"eval-result-{timestamp}.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"Results saved to: {result_path}")

    # Print summary
    _print_summary(result)

    return result


def _print_summary(result: dict) -> None:
    """Print a human-readable summary of evaluation results."""
    metrics = result.get("metrics", {})
    if not metrics:
        print("No metrics returned.")
        return

    print("\n" + "=" * 60)
    print("EVALUATION RESULTS SUMMARY")
    print("=" * 60)

    thresholds = {
        "relevance": 4.0,
        "task_adherence": 4.0,
        "intent_resolution": 4.0,
        "indirect_attack": 4.0,
        "tool_call_accuracy": 4.0,
    }

    for name, value in sorted(metrics.items()):
        threshold = thresholds.get(name.split(".")[0])
        status = ""
        if threshold is not None and isinstance(value, (int, float)):
            status = " ✅" if value >= threshold else " ❌"
        print(f"  {name}: {value}{status}")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Run Foundry built-in evaluators (Phase 1)"
    )
    parser.add_argument(
        "--dataset", "-d",
        required=True,
        help="Path to evaluation JSONL file",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=None,
        help="Output directory for results (default: .foundry/results/)",
    )
    parser.add_argument(
        "--evaluators", "-e",
        nargs="+",
        default=None,
        choices=["relevance", "task_adherence", "intent_resolution",
                 "indirect_attack", "tool_call_accuracy"],
        help="Which evaluators to run (default: all)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.dataset):
        print(f"Dataset file not found: {args.dataset}")
        sys.exit(1)

    run_evaluation(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        evaluator_names=args.evaluators,
    )


if __name__ == "__main__":
    main()
