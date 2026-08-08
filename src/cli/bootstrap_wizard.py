"""Back-compat shim. Real code lives in ``cli.wizard``.

Kept so external imports like ``from cli.bootstrap_wizard import BootstrapContext``
and any tooling that pickle-references this module path keep working.
"""

from __future__ import annotations

from cli.wizard import BootstrapContext, run_wizard
from cli.wizard.runner import (
    FULLSTACK_STEPS,
    PITCH_STEPS,
)

__all__ = ["FULLSTACK_STEPS", "PITCH_STEPS", "BootstrapContext", "run_wizard"]
