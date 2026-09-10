# Evaluation protocol and reporting contract

[Collection index](../README.md) · Supporting protocol for the
[strategy synthesis](../HARDY_STRATEGY_SYNTHESIS_2026-09-08.md), sections 5–9.
Applies to every suite; suite-specific labels remain distinct.

## Questions and units

Each experiment must name a capability, a concrete failure mode, its unit of
analysis, and evidence that could reject the proposed improvement. Do not pool
proofs, translations, retrieval queries, and multi-session research episodes
into one score. Preserve easy calibration items and build a hard tail of known
results, with field coverage reported alongside difficulty.

The basic unit is a problem or episode family. Repeated attempts and adversarial
variants from the same mathematical source are correlated observations, not
independent new problems. Report unique families, tasks, attempts, and completed
runs separately.

## Task and result records

A task manifest should identify:

- Suite and schema version; stable task and family IDs; corpus/source revision;
  exact input and imports; target/definition digests; field; selection criteria.
- Gold labels and their provenance; accepted alternative answers; reviewer
  identities/decisions; disagreements; adjudication date and label digest.
- Model-visible material, hidden material, permitted tools and libraries,
  intervention policy, and failure/termination conditions.
- Per-task budgets and the specific metric denominator.

Keep the scored outcome in a separate measurement record: condition ID,
attempt ID, task digest, environment and run-procedure digests, provider and
model identity, prompt/tool configuration hashes, memory/supplement snapshot,
random seed where supported, timestamps, artifacts, exact axioms, grade,
terminal reason, costs, and all unavailable measurements. Null cost is not zero.

A result must reference the evidence from which its labels can be recomputed.
Semantic judgments need their own recorded provenance; a mechanical validator
cannot recreate human meaning judgments from a hash.

## Conditions and controls

For a capability experiment, choose the smallest comparison that isolates it:
same model with and without retrieval, same fixed API with and without a
supplement, or same episode with and without durable state. Hold prompt
differences to the feature under study and record all differences.

For the overall harness comparison use ordinary chat, Lean feedback only, and
Hardy. Ask every condition to return a formal artifact for the same frozen
target. Apply the same external grading procedure to submitted artifacts.
If plain chat returns only prose, record no certified proof; separately report
whether it claimed completion. Never give its proof status a heuristic upgrade.

Alternate or randomize condition order within task families, using a recorded
schedule. Use fresh workspaces and explicit memory policies. Pin dependencies;
record provider changes or outages that prevent a contemporaneous comparison.
Do not treat a historical stronger-model result as an equal control.

## Budgets

Predeclare wall deadline, total model token/cost cap where enforceable, turn
limit, official Lean-check limit, per-check Lean limits, retrieval budget, and
CAS/replay budget as applicable. Record actual enforcement support by backend.
If a requested cap cannot be enforced, report that limitation or refuse that
condition rather than claiming a controlled comparison.

For parallel strategies, bound both total work and elapsed time. Include all
attempts, abandoned branches, model escalation, verification, and tool work.
Report setup/import time separately as well as end-to-end time. For library
creation and memory, show both initial creation cost and later amortized cost.

No live run or paid-provider call is authorized by this proposal.

## Splits and contamination

Split by source/concept/episode family before making paraphrases, false twins,
or sibling tasks. Keep all variants of a family in the same split. Freeze
development labels and prompts before examining the held-out outcomes.

Record direct theorem reuse, exact memory replay, related-family recall, and
held-out transfer separately. Hiding a theorem's title in the prompt does not
remove it from an imported library or the model's training data. Audit actual
prompts and tool responses for metadata leakage; state the contamination that
cannot be measured.

## Analysis

For binary outcomes, show numerator, denominator, and a 95% Wilson interval for
a simple per-task rate. For repeated or clustered tasks, use family-level
summaries or resampling over families; do not apply an independent-binomial
interval to every repeated attempt. For paired conditions, report the four
counts (both succeed, treatment only, control only, neither), the paired
difference, and uncertainty using a method specified before results.

For pass@k, fix k and the total allowed budget. Prefer direct groups of k
attempts with success defined as at least one certified proof. If estimating
from n samples with c successes, state the estimator
`1 - choose(n-c, k) / choose(n, k)`, require n >= k, and state the sampling
assumptions. Reused state or adaptive attempts are not independent samples;
report such a strategy as a budgeted search policy instead.

Report medians and tails for cost/time, both across all initiated attempts and
conditional on success where relevant. Timeouts remain failures for fixed-budget
solve rate and censored observations for time-to-solution analysis. Do not
replace them with successful-run medians. Expose model/provider errors,
malformed artifacts, verifier failures, interruptions, and missing data by type.

Report assigned-task completion and operational failure alongside capability
rates. Any policy excluding infrastructure failures must be predeclared and
also show the all-assigned denominator. Do not silently rerun failures until
they disappear. Do not pool across incompatible procedure or environment
digests to increase sample size.

## Pilot, expansion, and decision

Start with a small balanced fixture pilot to validate labels and instrumentation,
then estimate variance, cost, and a meaningful effect size for the next run.
Choose the held-out sample size using those values and the planned analysis;
there is no defensible universal “ten tasks is enough” threshold.

For each feature, predeclare a useful improvement threshold in its primary
metric, an acceptable cost increase, and trust/claim-drift guardrails. Pilot
results establish feasibility only. Do not tune the thresholds after seeing
the held-out comparison.

Zero observed false accepts is required in trust regressions but does not imply
a zero population error rate. Report the number and diversity of attacks and
uncovered surfaces. Small, correlated suites cannot establish research readiness
from a narrow confidence interval.

## Report layout and storage

Every report should contain the question; task/split manifest; conditions;
budget enforcement; primary paired results with uncertainty; failure taxonomy;
costs and missingness; per-task evidence links; deviations; and limitations.
Provide field-level and difficulty-level breakdowns without inventing one
cross-suite headline. Keep exploratory subgroup results labeled exploratory.

Harness/schema/testing work belongs on main-based code branches. Corpus content,
faithfulness decisions, releases, and corpus measurements follow
the corpus curation branch. Existing local `evals/` data are ignored and regenerable;
do not claim they are committed evidence. Any durable publication of a new
experiment needs a separately chosen, documented artifact destination.
