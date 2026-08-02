# Contributing

VESTIGIA uses a lightweight inspection-first collaboration flow.

1. Branch from current `main`; use a short descriptive branch name.
2. Explain implementation changes and any policy or resident-experience changes in the PR.
3. Add or update tests and documentation with the code.
4. Let CI run the unit suite, package build, isolated wheel import, and compile checks.
5. Resolve review questions before merge. Squash merge is the default unless commit history is
   itself useful evidence.
6. Version bumps, changelog entries, tags, and verified artifacts belong to explicit releases.
   Merged `main` between tags is unreleased development canon.

Technical mechanisms can carry hidden policy decisions. Call those out directly—especially
changes involving identity, context visibility, memory authority, consent, or outward action.
