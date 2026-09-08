# Overview evidence

Start at [the repository summary](../summary.html). The package map is
[code-overview.json](../code-overview.json); each of its eleven domains has
five linked pages under `docs/packages/`.

The report describes the refactored source at `d1bcc7c`. Health is a density of
detector findings over the mapped Python domains, not a count of demonstrated
runtime bugs. Candidates do not affect the grade. The repository atlas also
includes tests and supporting tooling; shell code is not assigned a Python
health grade. The three theory judges worked independently on each domain and
the repository. Their statements and disagreements matter more than the score;
the judges share a model family, so agreement does not remove model bias.

The measurement inventory records seven evaluation and faithfulness questions.
Its scope is explicit: it does not claim an exhaustive statistical audit of
every capability. Three ignored local boards contain 180 historical attempts,
60 per model; none matches the current procedure identity. They are not pooled
as a comparative estimate. The local 1,166-entry baseline is stale, while main
contains 20 candidate statements and no active headline denominator. No corpus
content or historical source identity was rewritten.

[Verification](verification.json) records the full test run and its failure
accounting. Combined line/branch coverage is 89.67%; atlas coverage tabs show
line coverage, a different quantity. The only new full-suite failure was an
installer line-ending change, subsequently repaired and retested. Remaining
failures reproduce Windows baseline limitations or stale local evaluation data.
An installed wheel outside the checkout passed its resource, CLI, deterministic
workflow, algebra-helper and MCP checks.

[Provenance](provenance.json) records the source snapshot and analyzer scope.
The unchanged skill analyzers supplied inventory, churn, coverage and report
rendering. Small local adaptations supplied canonical package context to scoped
import resolution and filtered coverage by exact path. Mixed import/resource
cycles and keyword-expanded model requests require the interpretation supplied
in the atlas's authored tabs. Churn on newly moved paths understates history
that predates the rename. Unsupported Lean, template and platform-script files
are named as limits, rather than treated as analyzed source.

The retained JSON inputs make the measurements and theory panel disputable:

- `measurement-inventory.json`: definitions, consumers, sample counts and limits.
- `board-observations.json`: counted historical records and compared identities.
- `theory/<domain>/judge*.json`: the three independent judgments.
- `analyzer-provenance.json`: exclusions used for the single root doctor run.

These are local documentation artifacts. They are neither benchmark evidence for
the refactored code nor a claim that generated artifacts execute in isolation.
