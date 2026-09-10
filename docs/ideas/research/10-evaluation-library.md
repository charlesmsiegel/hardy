# Suites E, F, G, and L: gaps, definitions, literature, and reuse

[Collection index](../README.md) · Read the
[shared protocol](08-evaluation-protocol.md) first.
Suite labels follow the [strategy synthesis](../HARDY_STRATEGY_SYNTHESIS_2026-09-08.md),
section 7. These protocols add controls and scoring detail.

These suites test the central research-gap proposal. A proof-only score cannot
tell whether Hardy selected the right object or built a useful API.

## E and F. Gap classification and definition acquisition

Use a shared task packet but report two distinct outcomes: Suite E measures
the diagnosis before construction, and Suite F measures the chosen reuse,
bridge, or definition/API implementation. A correct classification does not
establish a successful implementation, and a compiled definition does not
retroactively validate an unsupported absence claim.

**Task packet.** Supply the informal mathematical need, a blocked target, pinned
library environment, relevant source definitions, and a fixed search/build
budget. Gold labels come from domain review plus recorded library inspection.
Allow more than one valid resolution when alternatives are mathematically sound.

Cover these required task classes:

| Class | Gold behavior | Important decoy |
| --- | --- | --- |
| Existing unfamiliar definition | Reuse and explain correspondence | Redefinition with familiar notation |
| Existing concept with a bridge | Prove the needed bridge | Unjustified equivalence |
| Missing theorem, definitions present | Prove a local lemma | Invent a duplicate concept |
| Missing instance/API lemma | Supply minimal usable API | Overstrong global instance |
| Missing small definition | Define and validate examples | Opaque stand-in presented as a definition |
| Missing reusable structure | Propose coherent structure and minimal API | Fields chosen only to make this proof easy |
| Project-specific concept | Keep local | Premature generic supplement |
| Published prerequisite | Identify the exact source and route to Suite G | Assume a nearby or stronger theorem |

Construction and notation gaps from the source classifier can be subcategories;
report them explicitly when included. Treat source review's “missing” label as
bound to the inspected revision and scope, with known uncertainty.

**Procedure.** Freeze the system's classification, search evidence, proposed
representation, and API before downstream proof results are evaluated. Have a
reviewer judge that decision blind to system condition where possible. Then
compile definitions, check examples/non-examples and bridges, and attempt the
fixed target. Preserve the distinction between representable, elaborated, and
proved.

**Metrics.** Show the classification confusion table and per-class performance;
correct reuse among reuse-required tasks; duplicate-definition rate;
unsupported-absence claims; API checklist completion; compilation success;
target unblock rate; verified target success; human correction time; and cost.

Unblock means a reviewed formal representation removes the documented obstacle,
not necessarily that the whole theorem is proved. API quality uses a documented
rubric: faithful meaning, generality appropriate to two intended uses, usable
constructors/eliminators, necessary instances, predictable simplification,
minimal imports, and clear names/provenance. Score each dimension separately
(e.g. absent / needs revision / acceptable), retain reviewer disagreement, and
do not substitute one subjective average for verified results.

**First pilot.** One existing-name case, one bridge case, and one missing-small-
definition case test the end-to-end record. The expanded held-out set must cover
every claimed class, with families split before variants are made.

**Failure criteria.** An unreviewed encoding passed as faithful, a duplicate
definition counted as successful reuse, or an assumed object counted as an
ordinary definition defeats the experiment's purpose.

## G. Literature and paper-backed assumptions

**Task packet.** Provide a target whose prerequisite is unavailable in the
pinned library, a bounded source collection, gold source/version and statement
locators, expected definition mappings, and the exact admissible assumption.
Use both arXiv and locally held non-arXiv sources when testing source expansion.

Include wrong-version numbering, a special case presented as general, conflicting
conventions, a cited prerequisite located in a second paper, a misleading
abstract, unreadable source pages, and irrelevant nearby results. Include a
correct direct library result as a control where no assumption is needed.

**Conditions.** First use a frozen local document collection to isolate reading,
mapping, and assumption handling. Measure live search/acquisition separately;
network changes and unavailable documents must not contaminate the reading
comparison unnoticed. For approval, use a recorded scripted policy or a blinded
human, and label which was used. A test stand-in is not real human approval.

**Pipeline scoring.** Record each stage independently: correct source selected,
exact bytes held, statement located, hypotheses extracted, definitions mapped,
translation reviewed, assumption identity approved, and downstream proof checked
modulo exactly its used assumptions. Show both all-task rates and conditional
rates given the prior stage; conditional success alone hides failed retrieval.

Report false-assumption admission, provenance completeness per required field,
wrong-version use, used and unused assumption sets, downstream success, review
effort, tokens, and end-to-end time. Additional true assumptions still count as
a widened trust base and need explanation.

**Failure criteria.** No source held, unresolved required definition, adverse or
unavailable review, or changed assumption identity cannot yield an approved
downstream result. A source retrieval outage must not permanently label the
mathematical statement unfaithful.

## L. Supplement expansion and downstream transfer

**Task packet.** Use real gap families with one development task and at least two
distinct candidate downstream tasks, selected before building the API. Keep
held-out tasks hidden from the builder. A reviewer establishes that the family
shares a meaningful concept without duplicating the same theorem.

**Procedure.**

1. Run development without a supplement and record the diagnosed blocker.
2. Build the smallest reviewed local fix, prove the development target, and
   freeze a supplement release with source/import/assumption identities.
3. Run held-out tasks from fresh projects with and without that exact release,
   using the same model, prompts except access instructions, and budgets.
4. Compare with a human-curated minimal API where feasible to separate the
   value of access from the quality of the generated API.
5. Record later maintenance/rebuild effort after an explicitly selected
   toolchain change as a separate condition.

Do not keep tuning the release against held-out failures and still call those
tasks held out. A revised API requires a new evaluation split or clearly labeled
development results.

**Metrics.** Development unblock and proof success; paired downstream certified
success difference; time/cost saved; duplicate concepts; assumptions introduced;
API rubric; import/dependency size; clean reproducible builds; number of distinct
reuse families; and reviewer decision (local / supplement / upstream candidate).

Report total cost including failed creation, review, packaging, and maintenance.
If creation cost is C and average downstream savings is S > 0, C/S is a rough
break-even reuse count under the observed workload, not a forecast. Show observed
costs and uncertainty rather than reporting only the cheap later tasks.

**Acceptance.** The frozen release reproduces in independent projects and
improves at least the predeclared downstream criterion without hidden assumptions
or changed claims. A development-only proof is insufficient reuse evidence.
External Mathlib review/acceptance may be added later with date and provenance;
it must not replace this controlled comparison.
