"""What this machine actually has, as the doctor already probes it.

Every check is reported, including the ones that failed: `not found on PATH` is
a fact about the host and omitting it would leave the page unable to say why a
tool is unavailable. `deep` stays false -- opening a page must not start a long
probe, and the deep checks are what `hardy doctor --deep` is for.
"""

from __future__ import annotations

from typing import Any

from hardy.app import doctor
from hardy.app.config import Config


def environment(config: Config) -> dict[str, Any]:
    checks = doctor.run_checks(config, deep=False)
    return {
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail, "required": check.required}
            for check in checks
        ],
        "failures": sum(1 for check in checks if check.required and not check.ok),
    }
