from __future__ import annotations

from pathlib import Path

import pytest

from investment_agent.cli import async_main
from investment_agent.models import Cadence


@pytest.mark.asyncio
async def test_dry_run_ignores_proxy_environment_and_never_constructs_http_client(
    tmp_path, monkeypatch
):
    portfolio = Path(__file__).parents[1] / "portfolio.example.yaml"
    monkeypatch.setenv("PORTFOLIO_PATH", str(portfolio))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("dry-run constructed a live HTTP client")

    monkeypatch.setattr("investment_agent.cli.httpx.AsyncClient", ForbiddenClient)
    assert await async_main(Cadence.DAILY, dry_run=True) == 0
    assert list((tmp_path / "reports").glob("daily-*.md"))
    assert list((tmp_path / "reports").glob("daily-*.html"))
    assert not (tmp_path / "data" / "state.json").exists()
