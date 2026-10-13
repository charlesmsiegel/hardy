"""Claude CLI credential check shared by setup and doctor."""

import json
import subprocess

from hardy.foundation.process import run_guarded


def check_login(cli: str, *, runner=run_guarded) -> tuple[bool, str]:
    """Whether the subscription is actually signed in.

    `--version` would prove only that the executable exists, and reporting that
    as authentication would let doctor call a logged-out machine ready — the
    failure would then arrive on the first model call instead of here.
    """
    try:
        finished = runner([cli, "auth", "status"], timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return (False, f"could not run {cli}: {error}")
    if finished.interrupted or finished.timed_out:
        return (False, f"{cli} auth status did not finish")
    if finished.returncode != 0 or finished.output_overflow:
        return False, "`claude auth status` failed; run `claude login`"
    try:
        status = json.loads(finished.stdout)
    except (json.JSONDecodeError, TypeError):
        return (False, "could not read `claude auth status`; run `claude login`")
    if not isinstance(status, dict):
        return False, "could not read `claude auth status`; run `claude login`"
    if status.get("loggedIn") is not True:
        return (False, "not signed in; run `claude login` to use your subscription")
    return (True, f"signed in via {status.get('authMethod', 'unknown method')}")

