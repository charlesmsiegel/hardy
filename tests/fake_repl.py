"""A protocol-faithful fake of leanprover-community/repl for unit tests.

Speaks blank-line-delimited JSON over stdio like the real repl. Magic
commands drive failure modes:
  HANG      — never responds (timeout tests)
  DIE       — exits immediately (crash tests)
  BADJSON   — responds with non-JSON garbage (protocol-error tests)
  BADSCHEMA — responds with valid JSON that fails schema validation
  HUGE      — responds with a single ~1 MB JSON line (frame-limit tests)
  FLOOD     — emits many short lines with no blank separator (cumulative
              frame-limit tests)
  ERROR     — responds with an error message
  FATAL     — responds with a fatal repl-level message and no env
  sorry     — any cmd containing "sorry" responds with a sorries entry
  SHOW_ENV  — echoes the request's "env" field back as a warning message
Anything else gets {"env": N} with N incrementing per command.
"""

import json
import sys
import time


def read_request():
    lines = []
    for line in sys.stdin:
        if line.strip() == "":
            if lines:
                break
            continue
        lines.append(line)
    if not lines:
        return None
    return json.loads("".join(lines))


def respond(obj):
    sys.stdout.write(json.dumps(obj) + "\n\n")
    sys.stdout.flush()


def main():
    env = 0
    while True:
        req = read_request()
        if req is None:
            return
        if "cmd" in req:
            cmd = req["cmd"]
            if cmd == "HANG":
                time.sleep(3600)
            if cmd == "DIE":
                sys.exit(1)
            if cmd == "FATAL":
                # Fatal repl-level error (e.g. unknown environment): message,
                # no env — the worker can no longer serve the base environment.
                respond({"message": "unknown environment 0"})
                continue
            if cmd == "BADJSON":
                sys.stdout.write("this is not json\n\n")
                sys.stdout.flush()
                continue
            if cmd == "BADSCHEMA":
                respond(
                    {
                        "env": 0,
                        "messages": [
                            {
                                "severity": "catastrophic",
                                "pos": {"line": 1, "column": 0},
                                "data": "not a known severity",
                            }
                        ],
                    }
                )
                continue
            if cmd == "FLOOD":
                # Many short lines, each well under the per-line limit, and no
                # blank separator: exercises the cumulative frame bound.
                for _ in range(200000):
                    sys.stdout.write("x" * 50 + "\n")
                sys.stdout.flush()
                continue
            if cmd == "HUGE":
                data = "x" * (1 << 20)
                sys.stdout.write(
                    '{"env": 0, "messages": [{"severity": "warning", '
                    '"pos": {"line": 1, "column": 0}, "data": "' + data + '"}]}\n\n'
                )
                sys.stdout.flush()
                continue
            resp = {"env": env}
            env += 1
            if cmd == "ERROR":
                resp["messages"] = [
                    {
                        "severity": "error",
                        "pos": {"line": 1, "column": 0},
                        "endPos": {"line": 1, "column": 5},
                        "data": "unknown identifier 'ERROR'",
                    }
                ]
            if "sorry" in cmd:
                resp["sorries"] = [
                    {
                        "pos": {"line": 1, "column": 0},
                        "endPos": {"line": 1, "column": 5},
                        "goal": "⊢ True",
                        "proofState": 0,
                    }
                ]
            if cmd.startswith("SHOW_ENV"):
                resp["messages"] = [
                    {
                        "severity": "warning",
                        "pos": {"line": 1, "column": 0},
                        "data": f"env={req.get('env')}",
                    }
                ]
        elif "tactic" in req:
            resp = {"proofState": req["proofState"] + 1, "goals": []}
        else:
            resp = {"message": "unrecognized request"}
        respond(resp)


if __name__ == "__main__":
    main()
