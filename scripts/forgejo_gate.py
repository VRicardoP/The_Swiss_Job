#!/usr/bin/env python3
"""Publica el CI de GitHub en Forgejo y bloquea releases no aprobadas."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


CONTEXT = "dt144/github-swissjob-ci"
REPOSITORY = "VRicardoP/The_Swiss_Job"
OWNER = "VRicardoP"
REQUIRED_JOBS = {
    "backend-lint",
    "backend-test",
    "compose-config",
    "core-lint",
    "core-test",
    "docker-build",
    "frontend-lint",
    "frontend-test",
    "source-ownership",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class GateError(RuntimeError):
    pass


def _request_via_ssh(
    url: str, token: str, *, method: str, payload: dict | None, host: str
):
    command = [
        "curl",
        "-fsS",
        "--max-time",
        "30",
        "-X",
        method,
        "-H",
        f"Authorization: token {token}",
        "-H",
        "Accept: application/json",
    ]
    if payload is not None:
        command.extend(
            ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
        )
    command.append(url)
    try:
        result = subprocess.run(
            ["ssh", host, shlex.join(command)],
            capture_output=True,
            check=True,
            text=True,
            timeout=45,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise GateError(f"falló {method} {url} vía SSH") from error


def request_json(
    url: str, token: str, *, method: str = "GET", payload: dict | None = None
):
    ssh_host = os.environ.get("FORGEJO_SSH_HOST", "nas")
    if url.startswith("http://127.0.0.1:3001/") and ssh_host:
        return _request_via_ssh(
            url, token, method=method, payload=payload, host=ssh_host
        )
    headers = {"Accept": "application/json", "Authorization": f"token {token}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise GateError(f"falló {method} {url}: {error}") from error


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise GateError(f"falta la variable {name}")
    return value


def valid_sha(value: str) -> str:
    value = value.lower()
    if not SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "el SHA debe contener exactamente 40 dígitos hexadecimales"
        )
    return value


def github_ci(sha: str, token: str) -> tuple[str, str, str]:
    base = f"https://api.github.com/repos/{REPOSITORY}"
    data = request_json(
        f"{base}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=10", token
    )
    runs = [run for run in data.get("workflow_runs", []) if run.get("head_sha") == sha]
    if not runs:
        return "error", "GitHub no tiene un run de CI para este SHA", ""

    run = max(runs, key=lambda item: (item.get("id", 0), item.get("run_attempt", 0)))
    target = run.get("html_url", "")
    if run.get("name") != "CI" or run.get("path") != ".github/workflows/ci.yml":
        return "error", "el workflow de GitHub no coincide con CI", target
    if run.get("event") != "push":
        return "error", "CI no procede del push exacto del SHA", target
    if run.get("status") != "completed":
        return "pending", "CI todavía no ha terminado", target
    if run.get("conclusion") != "success":
        return "failure", f"CI terminó en {run.get('conclusion')}", target

    jobs_data = request_json(
        f"{base}/actions/runs/{run['id']}/jobs?per_page=100", token
    )
    jobs = jobs_data.get("jobs", [])
    names = {job.get("name") for job in jobs}
    if jobs_data.get("total_count") != len(jobs) or names != REQUIRED_JOBS:
        return (
            "error",
            "el conjunto de jobs no coincide con los nueve obligatorios",
            target,
        )
    failed = [
        job.get("name", "sin nombre")
        for job in jobs
        if job.get("conclusion") != "success"
    ]
    if failed:
        return "failure", f"jobs no verdes: {', '.join(failed)}", target
    return "success", "CI verde (9/9 jobs)", target


def publish_ci(sha: str) -> None:
    state, description, target = github_ci(sha, required_env("GITHUB_TOKEN"))
    base = os.environ.get("FORGEJO_URL", "http://127.0.0.1:3001").rstrip("/")
    repository = quote(REPOSITORY, safe="/")
    request_json(
        f"{base}/api/v1/repos/{repository}/statuses/{sha}",
        required_env("FORGEJO_TOKEN"),
        method="POST",
        payload={
            "context": CONTEXT,
            "description": description,
            "state": state,
            "target_url": target,
        },
    )
    print(json.dumps({"context": CONTEXT, "sha": sha, "state": state}))
    if state != "success":
        raise GateError(description)


def check_release(sha: str) -> None:
    token = required_env("FORGEJO_TOKEN")
    base = os.environ.get("FORGEJO_URL", "http://127.0.0.1:3001").rstrip("/")
    repository = quote(REPOSITORY, safe="/")
    api = f"{base}/api/v1/repos/{repository}"
    branch = request_json(f"{api}/branches/main", token)
    if branch.get("commit", {}).get("id") != sha or branch.get("protected") is not True:
        raise GateError("el SHA no es el main protegido vigente de Forgejo")

    protection = request_json(f"{api}/branch_protections/main", token)
    exact = {
        "apply_to_admins": True,
        "block_on_official_review_requests": True,
        "block_on_outdated_branch": True,
        "block_on_rejected_reviews": True,
        "dismiss_stale_approvals": True,
        "enable_approvals_whitelist": True,
        "enable_merge_whitelist": True,
        "enable_push": False,
        "enable_status_check": True,
        "ignore_stale_approvals": False,
        "required_approvals": 1,
    }
    wrong = [key for key, expected in exact.items() if protection.get(key) != expected]
    if wrong:
        raise GateError(f"protección Forgejo incompleta: {', '.join(wrong)}")
    if protection.get("merge_whitelist_usernames") != [OWNER]:
        raise GateError("sólo el propietario debe poder fusionar")
    if protection.get("approvals_whitelist_username") != [OWNER]:
        raise GateError("sólo el propietario debe poder aprobar")
    if protection.get("status_check_contexts") != [CONTEXT]:
        raise GateError(f"el único check requerido debe ser {CONTEXT}")

    combined = request_json(f"{api}/commits/{sha}/status", token)
    statuses = {
        status.get("context"): status.get("state")
        for status in combined.get("statuses", [])
    }
    if statuses.get(CONTEXT) != "success":
        raise GateError(f"{CONTEXT} no está verde para {sha}")
    print(json.dumps({"protected_main": sha, "required_status": CONTEXT}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("publish-ci", "check-release"):
        command = subparsers.add_parser(name)
        command.add_argument("sha", type=valid_sha)
    args = parser.parse_args()
    try:
        if args.command == "publish-ci":
            publish_ci(args.sha)
        else:
            check_release(args.sha)
    except GateError as error:
        print(f"ABORTA: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
