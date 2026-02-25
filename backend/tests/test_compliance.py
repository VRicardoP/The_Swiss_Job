"""Tests for the ComplianceEngine service."""

from sqlalchemy.ext.asyncio import AsyncSession

from models.source_compliance import SourceCompliance
from services.compliance import ComplianceEngine


async def _create_source(
    db: AsyncSession,
    source_key: str = "test_source",
    method: str = "api",
    is_allowed: bool = True,
    robots_txt_ok: bool = True,
    auto_disable_on_block: bool = True,
) -> SourceCompliance:
    """Helper to create a source compliance record."""
    source = SourceCompliance(
        source_key=source_key,
        method=method,
        is_allowed=is_allowed,
        robots_txt_ok=robots_txt_ok,
        auto_disable_on_block=auto_disable_on_block,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


class TestCanScrape:
    async def test_allowed_source(self, db_session: AsyncSession):
        await _create_source(db_session, "jooble", is_allowed=True, robots_txt_ok=True)
        engine = ComplianceEngine(db_session)
        assert await engine.can_scrape("jooble") is True

    async def test_disallowed_source(self, db_session: AsyncSession):
        await _create_source(db_session, "blocked", is_allowed=False)
        engine = ComplianceEngine(db_session)
        assert await engine.can_scrape("blocked") is False

    async def test_robots_txt_disallowed(self, db_session: AsyncSession):
        await _create_source(db_session, "no_robots", robots_txt_ok=False)
        engine = ComplianceEngine(db_session)
        assert await engine.can_scrape("no_robots") is False

    async def test_unknown_source(self, db_session: AsyncSession):
        engine = ComplianceEngine(db_session)
        assert await engine.can_scrape("nonexistent") is False


class TestReportBlock:
    async def test_increments_consecutive_blocks(self, db_session: AsyncSession):
        source = await _create_source(db_session, "test_inc")
        engine = ComplianceEngine(db_session)

        await engine.report_block("test_inc", 403)
        await db_session.refresh(source)
        assert source.consecutive_blocks == 1
        assert source.last_blocked_at is not None
        assert source.is_allowed is True

    async def test_auto_disable_after_threshold(self, db_session: AsyncSession):
        source = await _create_source(db_session, "test_disable")
        engine = ComplianceEngine(db_session)

        # Report 3 blocks (threshold)
        for _ in range(3):
            await engine.report_block("test_disable", 429)

        await db_session.refresh(source)
        assert source.consecutive_blocks == 3
        assert source.is_allowed is False

    async def test_no_auto_disable_when_disabled(self, db_session: AsyncSession):
        source = await _create_source(
            db_session, "test_no_auto", auto_disable_on_block=False
        )
        engine = ComplianceEngine(db_session)

        for _ in range(5):
            await engine.report_block("test_no_auto", 403)

        await db_session.refresh(source)
        assert source.consecutive_blocks == 5
        assert source.is_allowed is True  # Still allowed

    async def test_report_block_unknown_source(self, db_session: AsyncSession):
        engine = ComplianceEngine(db_session)
        # Should not raise
        await engine.report_block("nonexistent", 403)


class TestResetBlocks:
    async def test_resets_counter(self, db_session: AsyncSession):
        source = await _create_source(db_session, "test_reset")
        engine = ComplianceEngine(db_session)

        await engine.report_block("test_reset", 403)
        await engine.report_block("test_reset", 403)
        await db_session.refresh(source)
        assert source.consecutive_blocks == 2

        await engine.reset_blocks("test_reset")
        await db_session.refresh(source)
        assert source.consecutive_blocks == 0


class TestGetComplianceStatus:
    async def test_returns_all_sources(self, db_session: AsyncSession):
        await _create_source(db_session, "source_a", method="api")
        await _create_source(db_session, "source_b", method="scraping")
        engine = ComplianceEngine(db_session)

        status = await engine.get_compliance_status()
        assert len(status) == 2
        keys = [s["source_key"] for s in status]
        assert "source_a" in keys
        assert "source_b" in keys

    async def test_empty_when_no_sources(self, db_session: AsyncSession):
        engine = ComplianceEngine(db_session)
        status = await engine.get_compliance_status()
        assert status == []
