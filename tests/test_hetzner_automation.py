"""Regression checks for an expired Hetzner Console browser session."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cli.hetzner import config
from cli.hetzner.automation import HetznerAutomation


def test_login_does_not_trust_projects_url_when_session_expired(monkeypatch):
    bot = HetznerAutomation()
    wait_for_projects = AsyncMock()
    locator = MagicMock()
    locator.first.wait_for = wait_for_projects
    goto = AsyncMock()
    monkeypatch.setattr(
        bot,
        "_page",
        SimpleNamespace(
            url=config.HETZNER_PROJECTS_URL,
            goto=goto,
            locator=MagicMock(return_value=locator),
        ),
    )
    monkeypatch.setattr(bot, "_projects_page_ready", AsyncMock(return_value=False))

    assert asyncio.run(bot.login()) is True
    assert [call.args[0] for call in goto.await_args_list] == [
        config.HETZNER_PROJECTS_URL,
        config.HETZNER_LOGIN_URL,
    ]
    wait_for_projects.assert_awaited_once()


def test_login_reuses_session_only_when_projects_are_visible(monkeypatch):
    bot = HetznerAutomation()
    goto = AsyncMock()
    monkeypatch.setattr(bot, "_page", SimpleNamespace(goto=goto))
    monkeypatch.setattr(bot, "_projects_page_ready", AsyncMock(return_value=True))

    assert asyncio.run(bot.login()) is True
    goto.assert_awaited_once_with(
        config.HETZNER_PROJECTS_URL, wait_until="domcontentloaded"
    )
