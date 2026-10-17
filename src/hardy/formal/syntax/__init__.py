"""Pure Lean source scanning and module dependency graphs.

These lexical checks preserve the source identity; they do not elaborate
or verify it. Workspace builds and document readers share this one grammar.

The package splits it by owner, and each module imports the one it uses,
never this facade:

- `names`: identifier patterns, qualification and registry aliases.
- `lexer`: comments, literals, quotations and token boundaries, under every
  reading that could be Lean's.
- `scopes`: which namespace is open where.
- `scans`: theorems, lemmas, axioms and statements, and the refusals given
  instead of a guess.
- `imports`: the import header and the paths that name workspace modules.
- `dependencies`: build order, dependents and import cycles.

Everything that was importable from `hardy.formal.syntax` before the split
still is, from here.
"""
from __future__ import annotations

from hardy.formal.syntax.dependencies import Compile, ImportCycle, build_order, dependents
from hardy.formal.syntax.imports import (
    HEADER_KEYWORDS,
    IMPORT_PREFIX,
    WorkspacePathError,
    external_imports,
    import_header,
    import_table,
    internal_imports,
    module_name,
    module_path,
    parse_imports,
    safe_relative,
)
from hardy.formal.syntax.imports import _olean_module as _olean_module
from hardy.formal.syntax.imports import _olean_relative as _olean_relative
from hardy.formal.syntax.lexer import (
    TOKEN_COMMANDS,
    Lexed,
    blank_bounded_quotations,
    declares_tokens,
    identifier_tokens,
    lex,
    normalise_lean,
    numeral_ends,
    strip_comments,
)
from hardy.formal.syntax.names import (
    ANY_NAME,
    COMPONENT,
    DECLARATION_NAME,
    ESCAPED,
    IDENTIFIER,
    MODULE,
    NOT_NAME_AFTER,
    NOT_NAME_BEFORE,
    QUALIFIED,
    QUALIFIED_NAME,
    declared_name,
    name_aliases,
    unqualified,
)
from hardy.formal.syntax.scans import (
    ANY_DECLARATION,
    ASSUMPTION,
    ATTRIBUTES,
    AXIOM_KEYWORD,
    BINDERS,
    CLOSERS,
    COMMAND,
    DECLARATION_KINDS,
    DECLARATION_MODIFIERS,
    MODIFIERS,
    OPENERS,
    OPENS_PROOF,
    PRIVATE,
    PROOF,
    WRAPPER,
    DeclarationRefused,
    DuplicateDeclaration,
    QuotedDeclaration,
    UncertainDeclaration,
    assumptions,
    declarations,
    named_declarations,
    statements,
    unreadable_assumptions,
    unreadable_structure,
)
from hardy.formal.syntax.scans import _scan as _scan
from hardy.formal.syntax.scans import _statement_end as _statement_end
from hardy.formal.syntax.scans import _word_at as _word_at
from hardy.formal.syntax.scopes import NOT_A_SCOPE_NAME, SCOPE_KEYWORDS

__all__ = [
    "ANY_DECLARATION",
    "ANY_NAME",
    "ASSUMPTION",
    "ATTRIBUTES",
    "AXIOM_KEYWORD",
    "BINDERS",
    "CLOSERS",
    "COMMAND",
    "COMPONENT",
    "DECLARATION_KINDS",
    "DECLARATION_MODIFIERS",
    "DECLARATION_NAME",
    "ESCAPED",
    "HEADER_KEYWORDS",
    "IDENTIFIER",
    "IMPORT_PREFIX",
    "MODIFIERS",
    "MODULE",
    "NOT_A_SCOPE_NAME",
    "NOT_NAME_AFTER",
    "NOT_NAME_BEFORE",
    "OPENERS",
    "OPENS_PROOF",
    "PRIVATE",
    "PROOF",
    "QUALIFIED",
    "QUALIFIED_NAME",
    "SCOPE_KEYWORDS",
    "TOKEN_COMMANDS",
    "WRAPPER",
    "Compile",
    "DeclarationRefused",
    "DuplicateDeclaration",
    "ImportCycle",
    "Lexed",
    "QuotedDeclaration",
    "UncertainDeclaration",
    "WorkspacePathError",
    "assumptions",
    "blank_bounded_quotations",
    "build_order",
    "declarations",
    "declared_name",
    "declares_tokens",
    "dependents",
    "external_imports",
    "identifier_tokens",
    "import_header",
    "internal_imports",
    "import_table",
    "lex",
    "module_name",
    "module_path",
    "name_aliases",
    "named_declarations",
    "normalise_lean",
    "numeral_ends",
    "parse_imports",
    "safe_relative",
    "statements",
    "strip_comments",
    "unreadable_assumptions",
    "unreadable_structure",
    "unqualified",
]
