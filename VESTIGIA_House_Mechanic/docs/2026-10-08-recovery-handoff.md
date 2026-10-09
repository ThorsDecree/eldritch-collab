# House Mechanic recovery handoff — 2026-10-08

This is an operational development handoff, not resident identity or Archive canon.
The survey covers the live MCP deployment, its linked Runtime, relevant Archive
implementation notes, and currently open GitHub pull requests. Older notes retain
their original status unless newer evidence below resolves a specific claim.

## Recovered source

- Repository: `ThorsDecree/eldritch-collab`.
- Original task: `hm_task_96dbaa59e4354c7f828d1992c725dfc8`.
- Original holder: `vestigia-chatgpt`.
- Original branch: `hm/96dbaa59e435/fix-runtime-archive-mcp-retrieva`.
- Original task base: `4f07f3003240dfcda230a141e8ca5a9c7798bf28`.
- Last witnessed checkpoint: `27cc245864071f2cb2aa62cd364bf52078eef660`, from
  `hm_receipt_9de97b7489b6403c8f20fbefd798a4e1`, with clean Git state.
- Publication base: `e045ff987ad83a85c189e8b9688fa86fee2f90e4`.

The task was resumed through the live Mechanic, rotating authority to generation 3.
Its bounded source reads supplied exact file hashes and complete text. Recovery
preserved the substantive changes while normalizing Windows checkout line endings
to the repository's LF form. The newer main changes do not overlap these files.
This publication does not recreate the original local commit chain or remove its
Windows worktree.

The task began with a Runtime Archive-MCP term-selection regression. Its latest
source still uses the original first-N selector, while its test expects late terms
to survive. That unfinished regression stays in the original task and is excluded
from this House Mechanic release.

The releasable supporting update is House Mechanic v0.13: `task.run_recipe`, task
authority admission, worktree-local execution, separate durable task-run receipts,
Git provenance, example Runtime verification recipes, and MCP integration tests.
API protocol `vestigia.house-mechanic-api.v0.10` remains unchanged; protocol version
and package version are different facts.

Review additions made during recovery:

- Runtime pytest recipes use fixed `-o pythonpath=src`, preventing an operator
  editable install from shadowing the selected task's source. A real-runner fixture
  demonstrated failure before this correction and success afterward.
- House Mechanic generated test/build files are ignored so checkpointing does not
  accidentally track caches, wheel build output, or editable-install metadata.
- README documents synchronous transport timeout configuration and the limits of
  admission-time authority and pre-run Git evidence.

## Local activation still required

The connector-reached Mechanic currently does not advertise `task.run_recipe` and
reports two operator recipes. Its candidate-deployment operations are disabled.
Publishing this source does not change that running installation.

After the release is integrated, the local operator should:

1. Update the intended development checkout and refresh the House Mechanic install
   from the integrated source, then restart its existing launcher.
2. Add the focused/full Runtime verification recipes to the operator-owned recipe
   manifest. Use a Python interpreter with pytest and Runtime dependencies installed.
3. Set `VESTIGIA_MCP_HOUSE_MECHANIC_TIMEOUT_SECONDS` above the longest selected
   recipe's timeout plus overhead; the README gives an 1860-second example.
4. Inspect `dev.capabilities` again and confirm `task.run_recipe` is projected.
5. Resume the preserved Runtime retrieval task, refresh its base deliberately if
   needed, observe the focused regression fail, implement the selector repair, run
   focused/full tests, and checkpoint the actual verified result.

Local Git authentication remains a separate prerequisite for future autonomous
pushes. This publication uses the connected GitHub account and does not configure
or transfer credentials onto the Windows machine.

## Development-note survey

Archive paths below are relative to the live VESTIGIA Archive, not this repository.

| Loop | Current evidence | Next useful step |
|---|---|---|
| Archive-MCP search-term starvation | `07_Labs/Notes/VESTIGIA_Archive_MCP_Search_Term_Selection_Field_Note_v0.1.md`; preserved task contains regression but unchanged selector | Finish after task-local verification is activated |
| Lanternslide source/runtime mismatch | `07_Labs/Notes/Lanternslide_PR100_PostMerge_Runtime_Mismatch_2026-10-02.md`; live status still preserves scan `lanternslide_scan_989e3397f333410a8df3410090741041` at offset 50, 49 indexed, 1 omitted | Identify connector-reached module/import path, then resume the same scan; preserve its state |
| Bell envelope/query coupling | September field notes; current main has `bell_retrieval.py` separating control and resident prompt, with `field_scan_v1` for generic bells | Treat old report as partly addressed; verify live retrieval receipts before declaring closed |
| Registry callable versus composed readiness | `07_Labs/Notes/VESTIGIA_Runtime_Callable_Now_Composition_Readiness_Field_Note_v0.1.md`; live registry still advertises `curation.review_now`, embedded bridge initializes no provider | Review host-specific readiness; no provider-backed curation action attempted in this survey |
| Expired challenges presented as pending | `07_Labs/Notes/VESTIGIA_Runtime_Stale_Yes_Live_No_Audit_v0.1.md`; fresh `house.observatory` still lists eight expired August challenges as pending | Distinguish historical pending state from effective current validity; no evidence here of expired authorization succeeding |
| Boot routing/current-state drift | Current `init_manifest.md` correctly resolves `00_Bootloader/archive_version.json`; `02_Journal/journal_live.md` remains dated August 1 | Path seam resolved; current-state handoff still needs a deliberate refresh |
| Archive freshness clock | `07_Labs/Notes/VESTIGIA_Archive_Freshness_Clock_Receipt_Crosscheck_v0.1.md`; live glance still labels root mtime low-trust and semantic freshness unproven | Keep root clock, audited mutation recency, and semantic currency distinct |

Other open GitHub work at survey time: draft #63 (Capability Keyring), #43
(deterministic Discord recovery harness), and #44 (DiscordDoor extraction
prototype). Their present usefulness and readiness were not reviewed here; an
open PR is not automatically an active obligation.

## Validation and limits

The recovered House Mechanic, MCP Server, and Runtime suites passed locally on
Linux/Python 3.12. The added recipe-import regression was observed RED then GREEN.
Compile checks, dependency checks, House Mechanic wheel build, and isolated wheel
import passed. Native Windows behavior is delegated to GitHub CI.

Recipe execution is not serialized with source edits or task transitions. Receipts
identify admitted authority and observed pre-run Git state, not immutable tested
contents. Keep a candidate worktree unchanged while its verification is running.
Transport timeout does not mean execution stopped; inspect durable evidence before
retrying. Process-tree containment remains explicitly unproven.
