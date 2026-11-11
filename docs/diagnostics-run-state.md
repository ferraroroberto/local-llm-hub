# Diagnostics run status

A diagnostics capture is one row in `runs`, and its `status` follows a declared lifecycle in [`src/diagnostics/run_state.py`](../src/diagnostics/run_state.py). Every writer goes through it.

| Status | Meaning |
| --- | --- |
| `running` | The capture is in progress. |
| `complete` | It reached its deadline, or was a one-shot, and was judged. |
| `stopped` | The owner stopped it, or the hub cancelled the task. |
| `failed` | The capture task raised. |
| `interrupted` | The hub died mid-capture; closed at the next startup, keeping what it recorded. |
| `pruned` | A closed run whose raw samples aged out; its summary rows stay. |

`settle()` derives how a capture ended from what its task saw (raised, stopped, or ran out). `transition()` is the only code that says which status may follow which: a running run may finish or be interrupted, a finished run may only be finished again with the same status (finalize is idempotent) or pruned. `store.finish_run()` applies it, so an illegal write raises `IllegalRunTransition` and leaves the row as it was.
