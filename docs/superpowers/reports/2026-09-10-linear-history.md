# Linear history and preserved verification identities

The unpublished history after `origin/main` (`dd239cba`) was linearized while
preserving original refs and item patches. The [complete mapping](2026-09-10-linear-history.json)
contains **60 main + 12 engineering + 4 evaluation = 76 item commits**, each with
equal stable patch identities. `origin/main..29275af` contains 76 commits and
**zero merge commits**. Documentation added after this snapshot is outside that
count.

Four integration merges were omitted: `cc35feb`, `acab2be`, `0063fd1` and
`788ad73`. Evaluation's duplicate fixture fix `a762bf8` was absorbed by equivalent
`23cc160` (original engineering item `033ce4c`), with equal patch identity.

## Snapshot and tree correspondence

| Original snapshot | Linear snapshot | Exact tree evidence |
| --- | --- | --- |
| Main `a18cfe2` | `6121c54` | Both trees `35b9160cd215621f451d3246bdae5748866053f1` |
| Engineering `10db4f6` | `73ea678` | Both trees `91d4bf0606499d818e9b31bdc2b5d01894734be4` |
| Evaluation `894765b` plus engineering `10db4f6` | `29275af` | `git merge-tree --write-tree` in either source order gives final tree `601d5e9d52adc57e8f38d26d2382ebc45b70d668` |

The evaluation tree includes engineering integration; it is not claimed identical
to the original evaluation branch alone. Original refs remain available as
`archive/pre-linear-main-a18cfe2`, `archive/pre-linear-engineering-10db4f6` and
`archive/pre-linear-evaluation-894765b`.

| Frequently referenced item | Original tested commit | Rewritten equivalent |
| --- | --- | --- |
| X0 save ordering | `ae80543` | `f08bfe4` |
| X5 provider admission | `09fcc2d` | `5499d1b` |
| X6 batch journal | `e3523c4` | `92c73c5` |
| X6 attributed reviews | `1953381` | `cf98a5f` |
| X4 terminal/live recovery | `f76cb18` | `b1de4f8` |
| V1 history | `794ca29` | `13b6ae9` |
| V3 benchmarks | `07d8e67` | `7c79a53` |
| V0 fixture index | `647c5fb` | `88642b4` |
| V2 certification | `894765b` | `29275af` |

Reports retain originally tested commit IDs. Equal patch identities and final
trees do not imply that every rewritten intermediate tree received a new test
run. The fresh integrated evaluation gate actually runs on `29275af`; its result
belongs in the [evaluation report](2026-09-10-evaluation.md). Engineering's rerun
names original source `f76cb18` in its [report](2026-09-10-engineering.md).
The mapping's saved untracked-report digest describes preservation at rebase
time, before later documentation edits.

Both required gates passed. Final integration uses a fast-forward through the
evaluation history; this report does not claim that landing has already occurred.
The linear engineering branch is not a separate landing.
