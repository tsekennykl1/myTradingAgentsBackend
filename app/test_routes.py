import json
import sys
import time
from typing import Any

import requests


BASE_URL = "http://127.0.0.1:8000"

RUN_PAYLOAD = {
    "ticker": "AAPL",
    "model": "deepseek-v4-flash",
    "start_date": "2024-06-01",
    "end_date": "2024-06-30",
    "interval": "1d",
    "strategy": "default",
    "params": {
        "llm_provider": "deepseek",
        "quick_think_llm": "deepseek-v4-flash",
        "deep_think_llm": "deepseek-v4-flash",
        "research_depth": "Shallow",
        "language": "English",
    },
}


class TestFailure(Exception):
    pass


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def check_status(response: requests.Response, expected: int | tuple[int, ...], label: str) -> None:
    if isinstance(expected, int):
        expected = (expected,)
    if response.status_code not in expected:
        raise TestFailure(
            f"{label} failed\n"
            f"Expected status: {expected}\n"
            f"Actual status: {response.status_code}\n"
            f"Response body:\n{response.text}"
        )


def get_json(response: requests.Response, label: str) -> Any:
    try:
        return response.json()
    except Exception as exc:
        raise TestFailure(
            f"{label} did not return valid JSON.\n"
            f"Response body:\n{response.text}"
        ) from exc


def print_ok(label: str, extra: str = "") -> None:
    suffix = f" -> {extra}" if extra else ""
    print(f"[OK] {label}{suffix}")


def print_warn(label: str, extra: str = "") -> None:
    suffix = f" -> {extra}" if extra else ""
    print(f"[WARN] {label}{suffix}")


def test_root() -> None:
    label = "GET /"
    resp = requests.get(f"{BASE_URL}/", timeout=15)
    check_status(resp, 200, label)
    data = get_json(resp, label)
    print_ok(label, pretty(data))


def test_health() -> None:
    label = "GET /health"
    resp = requests.get(f"{BASE_URL}/health", timeout=15)

    # health may be 200 or 503 depending on engine state
    check_status(resp, (200, 503), label)

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        data = get_json(resp, label)
        print_ok(label, f"status={resp.status_code}, body={pretty(data)}")
    else:
        print_ok(label, f"status={resp.status_code}, body={resp.text}")


def test_list_runs() -> list[dict[str, Any]]:
    label = "GET /runs"
    resp = requests.get(f"{BASE_URL}/runs", timeout=15)
    check_status(resp, 200, label)
    data = get_json(resp, label)
    if not isinstance(data, list):
        raise TestFailure(f"{label} expected a JSON list, got:\n{pretty(data)}")
    print_ok(label, f"{len(data)} runs found")
    return data


def test_create_run() -> str:
    label = "POST /runs"
    resp = requests.post(f"{BASE_URL}/runs", json=RUN_PAYLOAD, timeout=30)
    check_status(resp, (200, 201), label)
    data = get_json(resp, label)

    run_id = data.get("run_id")
    if not run_id:
        raise TestFailure(f"{label} missing run_id:\n{pretty(data)}")

    status = data.get("status")
    print_ok(label, f"run_id={run_id}, status={status}, body={pretty(data)}")
    return run_id


def test_get_run(run_id: str) -> dict[str, Any]:
    label = f"GET /runs/{run_id}"
    resp = requests.get(f"{BASE_URL}/runs/{run_id}", timeout=15)
    check_status(resp, 200, label)
    data = get_json(resp, label)
    print_ok(label, f"status={data.get('status')}")
    return data


def poll_run(run_id: str, timeout_seconds: int = 300, interval_seconds: int = 5) -> dict[str, Any]:
    print(f"[INFO] Polling run {run_id} for up to {timeout_seconds} seconds...")

    terminal_statuses = {"completed", "failed", "cancelled"}
    started = time.time()
    last_status = None

    while True:
        data = test_get_run(run_id)
        status = str(data.get("status", "")).lower()

        if status != last_status:
            print(f"[INFO] Run status changed: {last_status} -> {status}")
            last_status = status

        if status in terminal_statuses:
            return data

        if time.time() - started > timeout_seconds:
            raise TestFailure(
                f"Run {run_id} did not reach terminal status within {timeout_seconds} seconds.\n"
                f"Last payload:\n{pretty(data)}"
            )

        time.sleep(interval_seconds)


def optional_get(run_id: str, suffix: str, expected_content_types: tuple[str, ...] = ()) -> None:
    path = f"/runs/{run_id}/{suffix}"
    label = f"GET {path}"
    resp = requests.get(f"{BASE_URL}{path}", timeout=30)

    if resp.status_code == 404:
        print_warn(label, "route not available or artifact not generated")
        return

    check_status(resp, 200, label)

    if expected_content_types:
        content_type = resp.headers.get("content-type", "")
        if not any(x in content_type for x in expected_content_types):
            raise TestFailure(
                f"{label} returned unexpected content type.\n"
                f"Expected one of: {expected_content_types}\n"
                f"Actual: {content_type}"
            )

    print_ok(label, f"content-type={resp.headers.get('content-type', '')}")


def summarize_run_result(run_data: dict[str, Any]) -> None:
    status = run_data.get("status")
    print(f"\n[SUMMARY] Final run status: {status}")

    if str(status).lower() == "failed":
        error = run_data.get("error")
        logs = run_data.get("logs")
        if error:
            print("[SUMMARY] Error:")
            print(pretty(error))
        if logs:
            print("[SUMMARY] Logs:")
            print(pretty(logs))
    else:
        print("[SUMMARY] Run payload:")
        print(pretty(run_data))


def main() -> int:
    try:
        print("[INFO] Starting route integration test...\n")

        test_root()
        test_health()
        test_list_runs()

        run_id = test_create_run()
        final_run = poll_run(run_id)

        summarize_run_result(final_run)

        # Optional artifact endpoints
        optional_get(run_id, "market-data", ("application/json",))
        optional_get(run_id, "result.html", ("text/html",))
        optional_get(run_id, "price-chart.html", ("text/html",))
        optional_get(run_id, "price-chart.png", ("image/png", "application/octet-stream"))

        print("\n[PASS] Route integration test completed.")
        return 0

    except TestFailure as exc:
        print(f"\n[FAIL] {exc}")
        return 1
    except requests.RequestException as exc:
        print(f"\n[FAIL] Network/request error: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n[ABORTED] Interrupted by user.")
        return 130
    except Exception as exc:
        print(f"\n[FAIL] Unexpected error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())