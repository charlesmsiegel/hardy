"""Real UI entrypoints with a local, deliberately noncooperative provider.

Only provider construction and terminal input/output are replaced. No shutdown
callback is installed here: the application's entrypoint must install it.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

from close_probe import write


def main():
    root, ui, kernel_mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    from hardy.foundation import paths

    paths.global_dir = lambda: root / "user-hardy"
    from hardy.app import config, tui
    from hardy.app.commands import launch
    from hardy.app.projects import prepare_layout
    from hardy.workflows.interactive.session import MathematicsSession

    class BlockedProvider:
        model = "console-acceptance@test"
        backend = "local-test"
        endpoint = "none"
        session_id = None

        def __init__(self, **context):
            self.session = context["dispatch"].__self__

        def stream(self, text):
            session = self.session
            result = session.cas.run("ready.py", "print('kernel ready')", author="human")
            assert result.status == "ok", result
            kernel_pid = session.cas.session._kernel.process.pid
            if kernel_mode == "active":
                active = root / "active-cell.txt"
                source = (f"from pathlib import Path; import time; "
                          f"Path({str(active)!r}).write_text('running'); time.sleep(600)")
                threading.Thread(target=lambda: session.cas.run("blocked.py", source, author="human"),
                                 daemon=True).start()
                deadline = time.monotonic() + 10
                while not active.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert active.exists(), "CAS cell did not start"

            hwnd, window_class = None, None
            if os.name == "nt":
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.GetConsoleWindow.restype = ctypes.c_void_p
                hwnd = kernel32.GetConsoleWindow()
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
                name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, name, len(name))
                window_class = name.value
            write(root, "ready.json", {
                "pid": os.getpid(), "kernel_pid": kernel_pid, "hwnd": hwnd,
                "window_class": window_class, "ui": ui, "kernel_mode": kernel_mode,
                "transcript": str(session.workspace / "transcript.jsonl"),
            })
            threading.Event().wait()  # cancellation intentionally cannot release this turn
            return iter(())

        def cancel(self):
            pass

    def make(model=None, **context):
        return BlockedProvider(**context)

    # The native parent runs the base interpreter to avoid Windows' venv
    # redirector. CAS environment filtering deliberately strips PYTHONPATH;
    # bootstrap the real driver with the same test venv explicitly instead.
    packages = getattr(sys, "_hardy_console_test_packages", None)
    if packages is not None:
        import hardy
        from hardy.algebra.backends import SympyBackend

        package_root = str(Path(hardy.__file__).resolve().parent.parent)
        driver_bootstrap = (
            "import runpy,site,sys; "
            "site.addsitedir(sys.argv.pop(1)); sys.path.insert(0,sys.argv.pop(1)); "
            "sys.argv=['hardy.cas_driver',*sys.argv[1:]]; "
            "runpy.run_module('hardy.cas_driver',run_name='__main__')"
        )

        def driver_argv(self, command, max_output_bytes=256 * 1024):
            return (sys._base_executable, "-u", "-c", driver_bootstrap,
                    packages, package_root, str(max_output_bytes))

        SympyBackend.argv = driver_argv

    launch.session_runtime = lambda _: make
    settings = config.Config(
        model=BlockedProvider.model, lean_command=("missing-lean",), lean_project=None,
        lean_timeout=1, latex_command=("missing-latex",), root=root / "projects",
        project="console", project_context=False, compute_detach_seconds=0,
    )
    prepare_layout(settings)
    if ui == "web":
        from hardy.app.commands.web import _web
        from hardy.app.project_registry import ProjectRegistry
        from hardy.app.web import server
        # Seed a real existing project record; the web entrypoint refuses an
        # unrecorded project. This bootstrap has no CAS or provider activity.
        bootstrap = MathematicsSession(settings.layout.problem, make, settings.lean_command,
                                        settings.latex_command, lambda _: False)
        bootstrap.close()
        registry = ProjectRegistry(root / "registry.json", default_root=settings.root)
        registry.add(settings.layout.problem)
        entry = registry.find(str(settings.layout.problem))
        original = server.serve

        def serve(host, **kwargs):
            bound = original(host, **kwargs, serve_forever=False)
            url = f"http://127.0.0.1:{bound.server_port}"

            def submit():
                request = Request(url + "/api/input", data=json.dumps({"text": "blocked turn"}).encode(),
                                  headers={"Content-Type": "application/json", "Origin": url,
                                           "X-Hardy-Token": bound.token})
                with urlopen(request, timeout=10) as response:
                    assert response.status == 200

            threading.Thread(target=submit, daemon=True).start()
            bound.serve_forever()
            return bound

        server.serve = serve
        _web(settings, parser=argparse.ArgumentParser(), args=argparse.Namespace(port=0, open=False),
             registry=registry, entry=entry)
    else:
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output.vt100 import Vt100_Output

        from hardy.app.commands.chat import _chat

        tui._is_interactive = lambda: True
        with create_pipe_input() as pipe:
            output = Vt100_Output(StringIO(), lambda: Size(rows=24, columns=80))
            with create_app_session(input=pipe, output=output):
                pipe.send_text("blocked turn\r")
                _chat(settings)


if __name__ == "__main__":
    main()
