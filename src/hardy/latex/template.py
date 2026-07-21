"""The standard writeup template (DESIGN.md Component 5), M0 edition.

Placeholders are <<TOKEN>> substituted with str.replace — NOT str.format or
string.Template, both of which collide with LaTeX's braces and math `$`.

M0 scope: no citations (the bibliography half of the contract is M3), and
informal completeness is hardcoded to "not assessed" (the critique-repair
loop that could grade it is M6; never default upward).
"""

FORMALIZATION_STATUSES = (
    "verified",
    "verified modulo assumed paper results",
    "partially formalized",
    "not formalized",
)

_TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsthm}
\newtheorem{theorem}{Theorem}

\title{<<TITLE>>}
\author{Hardy}
\date{}

\begin{document}
\maketitle

\begin{theorem}
<<STATEMENT>>
\end{theorem}

\begin{proof}
<<INFORMAL_PROOF>>
\end{proof}

\section*{Verification status}
\begin{itemize}
  \item Formalization status: <<FORMALIZATION_STATUS>>.<<LEAN_LINE>>
  \item Informal completeness: not assessed (critique--repair loop lands in M6).
\end{itemize}

\end{document}
"""


# LaTeX metacharacters that must be escaped inside \texttt{...}. Every char is
# mapped from the original string in one pass, so the braces/backslashes that
# the replacements introduce are never re-escaped.
_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape_path(path: str) -> str:
    return "".join(_LATEX_SPECIAL.get(ch, ch) for ch in path)


def render_writeup(
    *,
    title: str,
    statement: str,
    informal_proof: str,
    formalization_status: str,
    lean_file: str | None = None,
) -> str:
    if formalization_status not in FORMALIZATION_STATUSES:
        raise ValueError(
            f"unknown formalization status {formalization_status!r}; "
            f"expected one of {FORMALIZATION_STATUSES}"
        )
    lean_line = (
        f" Formal proof: \\texttt{{{_escape_path(lean_file)}}}." if lean_file else ""
    )
    doc = _TEMPLATE
    for token, value in {
        "<<TITLE>>": title,
        "<<STATEMENT>>": statement,
        "<<INFORMAL_PROOF>>": informal_proof,
        "<<FORMALIZATION_STATUS>>": formalization_status,
        "<<LEAN_LINE>>": lean_line,
    }.items():
        doc = doc.replace(token, value)
    return doc
