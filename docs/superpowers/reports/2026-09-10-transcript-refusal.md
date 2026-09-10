# V0 transcript refusal regression

Main's Linux [test run](https://github.com/charlesmsiegel/hardy/actions/runs/34484020663)
failed after a running session's transcript was replaced by a symlink.
The filesystem guard correctly refused the path. History refresh caught its
`LayoutError` as a generic `ValueError` and reclassified it as `SchemaError`.
Filesystem refusal and malformed history are separate existing public errors.

`SessionRecord._refresh_history` now preserves `LayoutError`, while continuing
to wrap invalid transcript structure. The existing actual-symlink test remains
unchanged. A portable guard-refusal fixture reproduces the exception boundary
on Windows without symlink privileges and asserts no history or file mutation.

The new test failed with the observed `SchemaError` before the fix. Afterwards,
conversation history, chat and terminal history tests passed: **46 passed,
4 skipped** (native symlink cases); Ruff passed. Linux actual-symlink validation
will run on the working branch before landing.
