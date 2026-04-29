"""Layer 3: Foundry Batch Eval API for CI/CD integration.

Submits evaluation jobs to the Foundry Batch Eval API for cloud execution.
Reads test cases from .foundry/agent-metadata.yaml and runs them against
the specified dataset and evaluators.

Usage:
  python evaluation/run_batch_eval.py
  python evaluation/run_batch_eval.py --priority P0
  python evaluation/run_batch_eval.py --test-case tc-happy-flow
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def _load_agent_metadata() -> dict:
    metadata_path = os.path.join(ROOT_DIR, ".foundry", "agent-metadata.yaml")
    with open(metadata_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_project_endpoint() -> str:
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ.get(
        "PROJECT_ENDPOINT"
    )
    if not endpoint:
        raise EnvironmentError(
            "Set AZURE_AI_PROJECT_ENDPOINT or PROJECT_ENDPOINT environment variable."
        )
    return endpoint


def _resolve_dataset_path(dataset_file: str) -> str:
    """Resolve dataset file path relative to project root."""
    path = os.path.join(ROOT_DIR, dataset_file)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset file not found: {path}")
    return path


def submit_batch_eval(
    test_cases: list[dict],
    project_endpoint: str,
) -> list[dict]:
    """Submit batch evaluation jobs to Foundry.

    Args:
        test_cases: List of test case dicts from agent-metadata.yaml
        project_endpoint: Foundry project endpoint URL

    Returns:
        List of evaluation run results.
    """
    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
    )

    results = []

    for tc in test_cases:
        tc_id = tc["id"]
        dataset_file = tc.get("datasetFile", "")
        dataset_path = _resolve_dataset_path(dataset_file)
        evaluator_configs = tc.get("evaluators", [])

        print(f"\n{'='*60}")
        print(f"Submitting batch eval: {tc_id}")
        print(f"  Dataset: {dataset_file}")
        print(f"  Evaluators: {[e['name'] for e in evaluator_configs]}")

        # Build evaluator config for the API
        evaluators = {}
        for ev in evaluator_configs:
            evaluators[ev["name"]] = {
                "threshold": ev.get("threshold", 4.0),
            }

        try:
            # Submit evaluation using the Foundry Batch Eval API
            eval_run = client.evaluations.create(
                data=dataset_path,
                evaluators=evaluators,
                description=f"CI eval: {tc_id} @ {datetime.now(timezone.utc).isoformat()}",
            )

            print(f"  Run ID: {eval_run.id}")
            print(f"  Status: {eval_run.status}")

            # Poll for completion
            run_result = _poll_eval_run(client, eval_run.id)
            run_result["test_case_id"] = tc_id
            run_result["evaluators"] = list(evaluators.keys())
            results.append(run_result)

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "test_case_id": tc_id,
                "status": "failed",
                "error": str(e),
            })

    return results


def _poll_eval_run(
    client: AIProjectClient,
    run_id: str,
    max_wait_seconds: int = 600,
    poll_interval: int = 15,
) -> dict:
    """Poll an evaluation run until completion."""
    elapsed = 0
    while elapsed < max_wait_seconds:
        run = client.evaluations.get(run_id)
        status = run.status

        if status in ("Completed", "completed"):
            print(f"  Completed in {elapsed}s")
            return {
                "run_id": run_id,
                "status": "completed",
                "metrics": getattr(run, "metrics", {}),
                "result": getattr(run, "result", None),
            }
        elif status in ("Failed", "failed", "Canceled", "canceled"):
            print(f"  Failed/Canceled after {elapsed}s")
            return {
                "run_id": run_id,
                "status": status.lower(),
                "error": getattr(run, "error", "Unknown error"),
            }

        print(f"  Polling... status={status} ({elapsed}s)")
        time.sleep(poll_interval)
        elapsed += poll_interval

    return {
        "run_id": run_id,
        "status": "timeout",
        "error": f"Timed out after {max_wait_seconds}s",
    }


def check_thresholds(
    results: list[dict],
    test_cases: list[dict],
) -> bool:
    """Check if all evaluation results meet threshold requirements.

    Returns:
        True if all pass, False if any fail.
    """
    all_passed = True
    tc_map = {tc["id"]: tc for tc in test_cases}

    print(f"\n{'='*60}")
    print("THRESHOLD CHECK RESULTS")
    print("=" * 60)

    for result in results:
        tc_id = result.get("test_case_id", "unknown")
        tc = tc_map.get(tc_id, {})
        status = result.get("status", "unknown")

        if status != "completed":
            print(f"  {tc_id}: {status} (FAIL)")
            all_passed = False
            continue

        metrics = result.get("metrics", {})
        evaluator_configs = tc.get("evaluators", [])

        for ev_config in evaluator_configs:
            ev_name = ev_config["name"]
            threshold = ev_config.get("threshold", 4.0)
            # Look for metric with the evaluator name
            metric_value = None
            for k, v in metrics.items():
                if ev_name in k and isinstance(v, (int, float)):
                    metric_value = v
                    break

            if metric_value is None:
                print(f"  {tc_id}/{ev_name}: metric not found (SKIP)")
                continue

            passed = metric_value >= threshold
            status_icon = "✅" if passed else "❌"
            print(
                f"  {tc_id}/{ev_name}: {metric_value:.2f} "
                f"(threshold: {threshold}) {status_icon}"
            )
            if not passed:
                all_passed = False

    return all_passed


def main():
    parser = argparse.ArgumentParser(
        description="Run Foundry Batch Eval for CI/CD"
    )
    parser.add_argument(
        "--priority",
        choices=["P0", "P1", "P2"],
        default=None,
        help="Only run test cases with this priority (default: all)",
    )
    parser.add_argument(
        "--test-case",
        default=None,
        help="Run a specific test case by ID",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON path for results",
    )
    args = parser.parse_args()

    metadata = _load_agent_metadata()
    env = metadata.get("environments", {}).get("dev", {})
    project_endpoint = _get_project_endpoint()
    test_cases = env.get("testCases", [])

    if not test_cases:
        print("No test cases found in .foundry/agent-metadata.yaml")
        sys.exit(1)

    # Filter test cases
    if args.test_case:
        test_cases = [tc for tc in test_cases if tc["id"] == args.test_case]
    elif args.priority:
        test_cases = [tc for tc in test_cases if tc.get("priority") == args.priority]

    if not test_cases:
        print("No matching test cases found.")
        sys.exit(1)

    print(f"Running {len(test_cases)} test case(s)")
    print(f"Project endpoint: {project_endpoint}")

    # Submit batch evaluations
    results = submit_batch_eval(test_cases, project_endpoint)

    # Check thresholds
    all_passed = check_thresholds(results, test_cases)

    # Save results
    output_path = args.output
    if not output_path:
        output_dir = os.path.join(ROOT_DIR, ".foundry", "results")
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = os.path.join(output_dir, f"batch-eval-{timestamp}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")

    if not all_passed:
        print("\n❌ Some evaluations did not meet thresholds.")
        sys.exit(1)
    else:
        print("\n✅ All evaluations passed.")


if __name__ == "__main__":
    main()
