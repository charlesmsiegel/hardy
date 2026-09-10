"""Proof-search strategy contracts.

Strategies describe bounded attempts over a frozen claim. They do not verify
proofs or assign formal grades; those remain formal-capability responsibilities.
"""

from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask, Strategy, run_strategy

__all__ = ("ProofOutcome", "ProofTask", "Strategy", "run_strategy")
