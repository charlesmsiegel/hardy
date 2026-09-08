"""Compatibility entry point; implementation lives in hardy.app.mcp."""
from .app.mcp import (
    LeanToolRuntime as LeanToolRuntime,
)
from .app.mcp import (
    configure_runtime as configure_runtime,
)
from .app.mcp import (
    lean_check_proof as lean_check_proof,
)
from .app.mcp import (
    lean_check_scratch as lean_check_scratch,
)
from .app.mcp import (
    lean_inspect_declarations as lean_inspect_declarations,
)
from .app.mcp import (
    lean_search_declarations as lean_search_declarations,
)
from .app.mcp import (
    load_runtime as load_runtime,
)
from .app.mcp import (
    main,
)
from .app.mcp import (
    mcp as mcp,
)
from .app.mcp import (
    rank_premises as rank_premises,
)
from .app.mcp import (
    register_cas_tools as register_cas_tools,
)

if __name__ == "__main__":
    raise SystemExit(main())
