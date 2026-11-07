"""Run versioned HTTP contract scenarios against POC and Ensemble.

The runner deliberately reports unavailable systems as NOT_RUN. It never converts
missing reference evidence into a pass.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer:
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must be empty or start with '/': {pointer}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _at_pointer(value: Any, pointer: str) -> Any:
    current = value
    for part in _pointer_parts(pointer):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


def compare_json(reference: Any, candidate: Any, policy: dict[str, Any] | None = None) -> list[str]:
    """Return deterministic JSON-pointer differences under an explicit policy."""
    policy = policy or {}
    ignored = set(policy.get("ignore_json_pointers", []))
    tolerances = policy.get("numeric_tolerances", {})
    differences: list[str] = []

    def walk(left: Any, right: Any, pointer: str) -> None:
        if pointer in ignored:
            return
        if pointer in tolerances:
            tolerance = float(tolerances[pointer])
            if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
                if abs(float(left) - float(right)) <= tolerance:
                    return
        if type(left) is not type(right):
            differences.append(f"{pointer or '/'}: type {type(left).__name__} != {type(right).__name__}")
            return
        if isinstance(left, dict):
            for key in sorted(set(left) | set(right)):
                child = pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
                if key not in left:
                    differences.append(f"{child}: missing from reference")
                elif key not in right:
                    differences.append(f"{child}: missing from candidate")
                else:
                    walk(left[key], right[key], child)
        elif isinstance(left, list):
            # Arrays are order-sensitive, but recurse so numeric tolerances and
            # ignored pointers still work for individual array elements.
            if len(left) != len(right):
                differences.append(f"{pointer or '/'}: list length {len(left)} != {len(right)}")
            for index, (a, b) in enumerate(zip(left, right)):
                walk(a, b, f"{pointer}/{index}")
        elif left != right:
            differences.append(f"{pointer or '/'}: {left!r} != {right!r}")

    walk(reference, candidate, "")
    return differences


def compare_observations(reference: dict[str, Any], candidate: dict[str, Any],
                         policy: dict[str, Any] | None = None) -> dict[str, Any]:
    differences: list[str] = []
    if reference.get("status_code") != candidate.get("status_code"):
        differences.append(
            f"HTTP status: {reference.get('status_code')} != {candidate.get('status_code')}"
        )
    if "body" in reference and "body" in candidate:
        differences.extend(compare_json(reference["body"], candidate["body"], policy))
    elif "body" in reference or "body" in candidate:
        differences.append("response body missing from one system")
    return {"status": "PASS" if not differences else "FAIL", "differences": differences}


def _request(base_url: str, spec: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/" + spec["path"].lstrip("/")
    body = spec.get("request")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=spec.get("method", "GET").upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except HTTPError as error:
        raw, status = error.read(), error.code
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"{type(error).__name__}: {error}") from error
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {"_raw_text": raw.decode("utf-8", errors="replace")}
    return {"status_code": status, "body": parsed}


def run_manifest(manifest: dict[str, Any], environ: dict[str, str] | None = None,
                 timeout: float = 10.0) -> dict[str, Any]:
    environ = os.environ if environ is None else environ
    results = []
    for scenario in manifest.get("scenarios", []):
        ref_spec, candidate_spec = scenario["reference"], scenario["candidate"]
        ref_env = ref_spec.get("base_url_env", manifest.get("reference_base_url_env", "POC_BASE_URL"))
        candidate_env = candidate_spec.get("base_url_env", manifest.get("candidate_base_url_env", "ENSEMBLE_BASE_URL"))
        ref_url, candidate_url = environ.get(ref_env), environ.get(candidate_env)
        result = {"id": scenario["id"], "capability": scenario["capability"]}
        if not ref_url or not candidate_url:
            result.update(status="NOT_RUN", reason=f"Set both {ref_env} and {candidate_env}.")
            results.append(result)
            continue
        try:
            reference = _request(ref_url, ref_spec, timeout)
            candidate = _request(candidate_url, candidate_spec, timeout)
            comparison = compare_observations(reference, candidate, scenario.get("comparison"))
            result.update(comparison)
            result["reference"] = reference
            result["candidate"] = candidate
        except RuntimeError as error:
            result.update(status="ERROR", reason=str(error))
        results.append(result)
    counts = {status: sum(item["status"] == status for item in results)
              for status in ("PASS", "FAIL", "NOT_RUN", "ERROR")}
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_version": manifest.get("version", 1),
        "summary": counts,
        "overall": "FAIL" if counts["FAIL"] or counts["ERROR"] else (
            "NOT_RUN" if counts["NOT_RUN"] else "PASS"
        ),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare POC and Ensemble HTTP contracts.")
    parser.add_argument("manifest", type=Path, help="Versioned JSON scenario manifest")
    parser.add_argument("--report", type=Path, default=Path("parity-report.json"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--strict", action="store_true",
                        help="Return non-zero when any scenario is NOT_RUN.")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = run_manifest(manifest, timeout=args.timeout)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "summary": report["summary"],
                      "report": str(args.report)}, indent=2))
    if report["summary"]["FAIL"] or report["summary"]["ERROR"]:
        return 1
    if args.strict and report["summary"]["NOT_RUN"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
