"""Read-only cross-artifact consistency checks for recorded runs.

An evidence reader: it refuses the records that could not have happened as
described, and it can launch nothing -- no provider, no controller, no
toolchain -- so a validation pass can neither spend money nor produce the
evidence it is judging. Split by the surface that wrote the run, and each
module imports its owner rather than this facade:

- `common`: strict source reads, the axiom allowlist, toolchain identity and
  spend fields, which both surfaces owe.
- `staged`: `hardy prove` runs -- manifest, Frozen Claim, verification
  evidence, faithfulness review and document.
- `batch`: `hardy batch` runs -- verdict, proof, closer ladder, sketch and
  refusal.
- `directory`: whichever kind of run a directory holds.

Everything importable from `hardy.workflows.recorded` before the split still
is, from here.
"""
from __future__ import annotations

from hardy.workflows.recorded.batch import (
    BATCH_FAILURES,
    REFUSALS,
    refusal_issues,
    validate_batch_consistency,
)
from hardy.workflows.recorded.batch import _attempt_issues as _attempt_issues
from hardy.workflows.recorded.batch import _axiom_line as _axiom_line
from hardy.workflows.recorded.batch import _closer_issues as _closer_issues
from hardy.workflows.recorded.batch import _discarded as _discarded
from hardy.workflows.recorded.batch import _proof_argument as _proof_argument
from hardy.workflows.recorded.batch import _read_json as _read_json
from hardy.workflows.recorded.batch import _renderable as _renderable
from hardy.workflows.recorded.batch import _sketch_issues as _sketch_issues
from hardy.workflows.recorded.batch import _sketch_source as _sketch_source
from hardy.workflows.recorded.batch import _verified_batch_issues as _verified_batch_issues
from hardy.workflows.recorded.common import (
    BATCH_SEARCH,
    IDENTITY_FIELDS,
    STAGED_SEARCH,
    USAGE_FIELDS,
    VERIFIED_GRADES,
    permitted_axioms,
)
from hardy.workflows.recorded.common import _toolchain_issues as _toolchain_issues
from hardy.workflows.recorded.common import _usage_issues as _usage_issues
from hardy.workflows.recorded.directory import validate_recorded_run
from hardy.workflows.recorded.staged import (
    ASSUMPTIONS_FILE,
    grades_agree,
    validate_run_consistency,
)
from hardy.workflows.recorded.staged import _declaration_issues as _declaration_issues
from hardy.workflows.recorded.staged import _declared as _declared
from hardy.workflows.recorded.staged import _declared_names as _declared_names
from hardy.workflows.recorded.staged import _faithfulness_issues as _faithfulness_issues
from hardy.workflows.recorded.staged import _lean_source_issues as _lean_source_issues
from hardy.workflows.recorded.staged import _live_staged_issues as _live_staged_issues
from hardy.workflows.recorded.staged import (
    _verification_record_issues as _verification_record_issues,
)
from hardy.workflows.recorded.staged import _verified_run_issues as _verified_run_issues

__all__ = [
    "ASSUMPTIONS_FILE",
    "BATCH_FAILURES",
    "BATCH_SEARCH",
    "IDENTITY_FIELDS",
    "REFUSALS",
    "STAGED_SEARCH",
    "USAGE_FIELDS",
    "VERIFIED_GRADES",
    "grades_agree",
    "permitted_axioms",
    "refusal_issues",
    "validate_batch_consistency",
    "validate_recorded_run",
    "validate_run_consistency",
]
