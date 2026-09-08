"""Compatibility entry point; implementation lives in hardy.app.mcp."""
from hardy.app.mcp import LeanToolRuntime as LeanToolRuntime
from hardy.app.mcp import configure_runtime as configure_runtime
from hardy.app.mcp import lean_check_proof as lean_check_proof
from hardy.app.mcp import lean_check_scratch as lean_check_scratch
from hardy.app.mcp import lean_inspect_declarations as lean_inspect_declarations
from hardy.app.mcp import lean_search_declarations as lean_search_declarations
from hardy.app.mcp import load_runtime as load_runtime
from hardy.app.mcp import main
from hardy.app.mcp import mcp as mcp
from hardy.app.mcp import rank_premises as rank_premises
from hardy.app.mcp import register_cas_tools as register_cas_tools

if __name__ == "__main__":
    raise SystemExit(main())
