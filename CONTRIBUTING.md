# Contributing

VESTIGIA uses a lightweight inspection-first collaboration flow.

1. **Roadmap & Issues**: All roadmap items require an open and discussed issue before implementation begins. Note that roadmap versions are directional and do not represent committed delivery dates.
2. **Branching**: Branch from current `main`; use a short descriptive branch name.
3. **PR Description**: Explain implementation changes, policy effects, or resident-experience changes in the PR.
4. **Tests & Docs**: Add or update tests and documentation alongside code changes.
5. **CI checks**: Verify that CI successfully compiles the source, runs pytest, builds packages, and executes isolated wheel tests.
6. **Review & Merge**: Resolve review questions before merge. Squash merge is the default unless commit history is itself useful evidence.
7. **Releases**: Version bumps, changelog entries, tags, and verified artifacts belong to explicit release operations. Merged `main` between tags is unreleased development canon.

## Policy-Changing Work Review

Technical mechanisms can carry hidden policy decisions. Any pull requests affecting the following fields are classified as policy-changing:
- Identity boundaries and plural isolation
- Context drawer visibility (e.g. ambient history)
- Memory creation, storage, and retrieval authority
- Resident consent prompts and image authorization
- Outward effect engines (e.g. Discord reaction delivery, bells)

Such work **must be explicitly called out** in the PR template/description, and requires thorough review by repository maintainers to verify that policy invariants are preserved.
