"""Lazy access to Playwright's exception type.

Playwright is optional at runtime: it ships with a normal CLI install but project
deployments prune it (see `[tool.uv] override-dependencies` in a deployment
pyproject), and `cli.cloudflare` must stay importable without it so that
`_require_playwright()` can print a friendly install hint instead of an
ImportError traceback. That rules out a module-level
`from playwright.async_api import Error`, so the browser modules resolve it here
on first use instead.

Use it directly in an except clause:

    except playwright_error():
        ...

Every Playwright failure derives from this class, `TimeoutError` included, so it
covers "the page did not do what we expected" — which is the normal, recoverable
case in these UI-probing flows — while letting real bugs in our own code
(typos, attribute access on None) propagate instead of being silently swallowed.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def playwright_error() -> type[BaseException]:
    """Return `playwright.async_api.Error`, importing Playwright on first call."""
    from playwright.async_api import Error

    return Error
