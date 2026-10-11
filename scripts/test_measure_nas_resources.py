import json

import pytest

from scripts.measure_nas_resources import (
    CAPTURE_CONTAINER,
    memory_mib,
    parse_stats,
    validate_capture_state,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("512 KiB", 0.5), ("128 MiB", 128), ("1.5 GiB", 1536)],
)
def test_memory_mib(value, expected):
    assert memory_mib(value) == expected


def test_parse_stats_uses_current_usage_and_numeric_fields():
    output = json.dumps(
        {
            "Name": "core",
            "CPUPerc": "1.25%",
            "MemUsage": "1.5GiB / 8GiB",
            "PIDs": "9",
        }
    )

    assert parse_stats(output) == {
        "core": {"cpu_percent": 1.25, "memory_mib": 1536, "pids": 9}
    }


@pytest.mark.parametrize("capture_retired", [False, True])
def test_validate_capture_state_accepts_matching_topology(capture_retired):
    names = set() if capture_retired else {CAPTURE_CONTAINER}

    validate_capture_state(names, capture_retired)


@pytest.mark.parametrize("capture_retired", [False, True])
def test_validate_capture_state_rejects_opposite_topology(capture_retired):
    names = {CAPTURE_CONTAINER} if capture_retired else set()

    with pytest.raises(RuntimeError):
        validate_capture_state(names, capture_retired)
