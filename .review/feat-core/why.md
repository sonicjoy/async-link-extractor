# Why this change exists

**Problem.** The repository holds an approved design (`docs/designs/linkx.md`, Status: Approved) and no code. The tool it describes is the deliverable: a command-line producer/consumer link extractor where fetching and parsing run concurrently through a bounded queue, one bad URL cannot affect the others, the oldest queue entries are trimmed when the queue grows too large, and the code is covered by tests.

**Evidence the problem is real.** The written requirement (summarised in the design's "The Problem" section) asks for exactly this tool, with tests, delivered as a public repository. Nothing in `main` implements it: `git ls-tree -r main --name-only` lists only the design documents and review reports.

**If it is never merged.** There is no deliverable: `main` has a design and nothing that runs.
