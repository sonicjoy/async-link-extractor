# Spec and plan

- **Spec:** `docs/designs/linkx.md` (Status: Approved, 2026-11-19). Sections Overview to Non-Functional Requirements.
- **Plan:** the same file, section "Detailed Implementation Plan" (steps 1 to 10 and the Verification Plan).
- **Background:** `docs/designs/linkx-brief.md` (concept review) and `.review/design/*.md` (design review reports).

The design was approved by the user before any code was written. Two component signatures were updated in this branch to match the code as built (`Sink(stream, jobs)` instead of `Sink(stream, total)`; `Fetcher` also takes `clock`); see commit f51d6b3.
