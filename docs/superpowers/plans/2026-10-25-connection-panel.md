# Connection Panel implementation plan

**Goal:** Add a resident-facing, redacted view of Runtime connections and a narrow control for
attached optional context sources.

**Architecture:** `CoreRuntime` binds its constructed provider and `ContextAssembler` source
instances to `HousePort`. `connection.panel` summarizes these live objects alongside safe
configuration and resident work. `connection.control` stores a resident-scoped optional-source
exclusion in the existing `resident_jobs` table; `ContextAssembler` applies it on the next
assembly.

## Tasks

1. Add a failing panel contract test, then implement its snapshot, contracts, and CoreRuntime
   binding. Verify secrets never appear and standalone HousePort does not claim live attachments.
2. Add failing source-control tests, then implement the resident preference store and retrieval
   gate. Verify required and unattached sources reject, and enable/disable affects later assembly.
3. Document the operator, resident, and context-source boundaries. Run targeted tests, full test
   suite, compile check, and `git diff --check`.

Final review refinements: pending counts omit tasks whose expiry time has passed without writing a
new task status; capability examples use a panel-sourced placeholder; the preference summary
includes private-image mode while omitting private pocket names.
