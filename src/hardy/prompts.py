"""Versioned instructions for Hardy's staged mathematics workflow.

The prompt set is hashed and the hash is recorded in every run manifest, so a
result can be traced to the exact instructions that produced it. Editing any
prompt below changes that hash, which is the intended effect.
"""

from __future__ import annotations

import hashlib
import json

from .domain import FrozenClaim

PROMPT_SET_VERSION = "2026-07-24.1"

BASE_INSTRUCTIONS = """
You are Hardy's mathematics agent. Work only on the requested stage, keep every
assumption explicit, and treat Lean feedback as authoritative for formal proof.
Never claim verification yourself: only Hardy's independent FinalVerifier can
accept a solution.
""".strip()

DEVELOPER_INSTRUCTIONS = """
Generated Lean and TeX are not sandboxed or inherently safe. Do not run unrelated
commands, add dependencies, change fixed imports, or access credentials. Return
the requested structured response and preserve incomplete work honestly.
""".strip()

FORMALIZATION_PROMPT = """
Translate the user's ordinary-language claim into one proposed Lean theorem.
Return a plain restatement, domains, quantifiers, assumptions, interpretation
choices, theorem name, binders, and proposition. Do not prove it. Do not silently
weaken or strengthen the claim.
""".strip()

WRITEUP_PROMPT = """
Write a clear human-readable mathematical exposition for the approved claim.
Separate proved facts from remaining gaps. The informal proof is not independently
assessed, and the document must not imply otherwise.
""".strip()

PROOF_PROMPT_TEMPLATE = """
Prove the immutable Frozen Claim {claim_hash}.

Exact Lean signature:
{signature}

Do not change the theorem name, binders, proposition, or imports.
Use the four Hardy tools as needed:
- lean_check_proof for official candidates against the Frozen Claim;
- lean_check_scratch for bounded exploration;
- lean_inspect_declarations for exact known names; and
- lean_search_declarations for discovery.

Return proof_body as the complete Lean term placed after :=, normally beginning
with by; never include a theorem or lemma declaration. Also return an informal
proof, clearly understood to be not independently assessed. A tool success is
feedback only: only Hardy's FinalVerifier can accept the solution.
""".strip()


def proof_prompt(claim: FrozenClaim) -> str:
    binders = f" {claim.proposal.binders.strip()}" if claim.proposal.binders.strip() else ""
    signature = (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()}"
    )
    return PROOF_PROMPT_TEMPLATE.format(claim_hash=claim.content_hash, signature=signature)


def _prompt_set_hash() -> str:
    payload = {
        "base": BASE_INSTRUCTIONS,
        "developer": DEVELOPER_INSTRUCTIONS,
        "formalization": FORMALIZATION_PROMPT,
        "proof": PROOF_PROMPT_TEMPLATE,
        "version": PROMPT_SET_VERSION,
        "writeup": WRITEUP_PROMPT,
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


PROMPT_SET_SHA256 = _prompt_set_hash()
