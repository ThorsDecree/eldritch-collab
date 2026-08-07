# Workshop Microscope

The Workshop Microscope is a resident-private, read-only view over the inert
script shelf.

It explains:

- immutable script identity and source hashes;
- lifecycle state and event history;
- authorship and supply provenance;
- static inspection signals and their limits;
- input/output contract diagnostics;
- sticky quarantine evidence;
- private version-to-version source differences.

It does **not** test, approve, activate, execute, unquarantine, or grant script
authority. All effective callability results are `false` while the Runtime has
no separately authenticated hardened execution backend.

The Microscope registers through the explicit composition layer. It does not
replace private shelf functions or Runtime methods.
