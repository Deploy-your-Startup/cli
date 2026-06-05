"""Bootstrap wizard package — guided pipeline for new startups.

Two flows live here:
  * fullstack: Domain → Hetzner → Project → Finalize  (Django + k3s + Hetzner)
  * pitch:     Domain → Cloudflare → Project → Finalize  (Astro → Cloudflare Pages)

Each step is its own module under ``wizard.steps.*``; the runner picks the
right list from ``ctx.kind``.
"""

from __future__ import annotations

from .context import BootstrapContext
from .runner import (
    FULLSTACK_STEPS,
    PITCH_STEPS,
    check_prerequisites,
    run_wizard,
    steps_for,
)

__all__ = [
    "BootstrapContext",
    "FULLSTACK_STEPS",
    "PITCH_STEPS",
    "check_prerequisites",
    "run_wizard",
    "steps_for",
]
