"""The old school-only rollback must not discard F-owned marks or clears."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from jobhunt_core.import_schools import SchoolMigrationError
from jobhunt_core.school_source import reverse_sync


@pytest.mark.parametrize(
    "mark",
    [
        {"feedback": "thumbs_up"},
        {"feedback": None, "feedback_recorded_at": "2026-09-19T10:00:00Z"},
        {"feedback_implicit": [{"action": "opened"}]},
    ],
)
@pytest.mark.parametrize("origin", ["portfolio", "swissjob"])
def test_school_only_reverse_refuses_new_feedback_before_any_io(mark, origin):
    session = AsyncMock()
    with pytest.raises(SchoolMigrationError, match="coordinated feedback rollback"):
        asyncio.run(
            reverse_sync(session, {}, origin, {}, {"school_applications": [mark]})
        )
    session.execute.assert_not_called()
