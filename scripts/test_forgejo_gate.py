from unittest.mock import patch
from subprocess import CompletedProcess

import pytest

from scripts import forgejo_gate


SHA = "a" * 40


def successful_run():
    return {
        "id": 7,
        "head_sha": SHA,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "html_url": "https://example.test/run/7",
    }


def test_forgejo_requests_use_nas_loopback_over_ssh():
    response = CompletedProcess([], 0, '{"protected": true}', "")
    with patch.object(forgejo_gate.subprocess, "run", return_value=response) as run:
        assert forgejo_gate.request_json(
            "http://127.0.0.1:3001/api/v1/version", "secret"
        ) == {"protected": True}
    command = run.call_args.args[0]
    assert command[:2] == ["ssh", "nas"]
    assert "http://127.0.0.1:3001/api/v1/version" in command[2]


def test_github_ci_requires_exact_nine_successful_jobs():
    jobs = [
        {"name": name, "conclusion": "success"} for name in forgejo_gate.REQUIRED_JOBS
    ]
    responses = [
        {"workflow_runs": [successful_run()]},
        {"total_count": len(jobs), "jobs": jobs},
    ]
    with patch.object(forgejo_gate, "request_json", side_effect=responses):
        assert forgejo_gate.github_ci(SHA, "token") == (
            "success",
            "CI verde (9/9 jobs)",
            "https://example.test/run/7",
        )


def test_github_ci_rejects_a_missing_job():
    jobs = [
        {"name": name, "conclusion": "success"}
        for name in forgejo_gate.REQUIRED_JOBS - {"source-ownership"}
    ]
    responses = [
        {"workflow_runs": [successful_run()]},
        {"total_count": len(jobs), "jobs": jobs},
    ]
    with patch.object(forgejo_gate, "request_json", side_effect=responses):
        assert forgejo_gate.github_ci(SHA, "token")[0] == "error"


@pytest.mark.parametrize(
    ("run_change", "expected"),
    [
        ({"status": "in_progress", "conclusion": None}, "pending"),
        ({"conclusion": "failure"}, "failure"),
        ({"event": "pull_request"}, "error"),
    ],
)
def test_github_ci_fails_closed_before_reading_jobs(run_change, expected):
    run = successful_run()
    run.update(run_change)
    with patch.object(
        forgejo_gate, "request_json", return_value={"workflow_runs": [run]}
    ):
        assert forgejo_gate.github_ci(SHA, "token")[0] == expected


def test_check_release_requires_protected_main_policy_and_status(monkeypatch):
    monkeypatch.setenv("FORGEJO_TOKEN", "token")
    protection = {
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
        "merge_whitelist_usernames": ["VRicardoP"],
        "approvals_whitelist_username": ["VRicardoP"],
        "status_check_contexts": [forgejo_gate.CONTEXT],
    }
    responses = [
        {"commit": {"id": SHA}, "protected": True},
        protection,
        {"statuses": [{"context": forgejo_gate.CONTEXT, "state": "success"}]},
    ]
    with patch.object(forgejo_gate, "request_json", side_effect=responses):
        forgejo_gate.check_release(SHA)


def test_check_release_rejects_unprotected_branch(monkeypatch):
    monkeypatch.setenv("FORGEJO_TOKEN", "token")
    with patch.object(
        forgejo_gate,
        "request_json",
        return_value={"commit": {"id": SHA}, "protected": False},
    ):
        with pytest.raises(forgejo_gate.GateError, match="main protegido"):
            forgejo_gate.check_release(SHA)
