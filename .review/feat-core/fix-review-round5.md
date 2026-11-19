# Fix review, round 5

**Verdict:** PASS

## Findings claimed fixed
| Finding | Property (one sentence) | Status | Evidence |
|---|---|---|---|
| First CI run failed in "Set up job": `astral-sh/setup-uv@v10` not resolvable | Every `uses:` reference must resolve to an existing ref | resolved | `gh api .../git/ref/tags/<tag>` returned type `commit` for all three (lightweight tags, so no annotated-tag dereference needed): checkout v7.0.1 = 3d3c42e5aac5ba805825da76410c181273ba90b1; setup-uv v10.3.3 = 68559e99cc807c1eb7e9238fd43a15bba460eec2; upload-artifact v7.0.2 = cf430e030ddbb5b0abf93d22962f4752f3646cd9. All match the SHAs in ci.yml. `refs/tags/v10` on setup-uv returns 404, confirming the cause. |
| Ledger: CI actions tag-pinned (security LOW) | Third-party actions are pinned to immutable commits | resolved | Same SHAs; all three actions pinned (no remaining tag-only `uses:` in ci.yml). |

## New behaviour introduced by the fixes
| Hunk | What now happens | Who is affected, and are they told |
|---|---|---|
| ci.yml pins | Actions no longer float to newer patch releases | Maintainers; the version is in a trailing comment. Updates need a manual bump (or Dependabot/Renovate, not configured here). Not a defect. |

## New findings
None. (Note, not a finding: `v10.3.3` and `v7.0.x` were chosen as the exact patch tags; I did not check they are the latest releases, only that they exist and the SHAs match.)

## Unclaimed changes
None. `git diff ce85b02..HEAD --stat`: `.github/workflows/ci.yml` (3 lines changed) and `.review/LEDGER.md` (1 row). The ci.yml diff has exactly 3 removed and 3 added lines, each only the `uses:` line. The ledger row changed only in its status column.

## Workflow validity
YAML parses (PyYAML). Top-level `permissions: {contents: read}` unchanged; same steps, names, `with:` inputs, matrix and conditions. Not run on GitHub Actions by me.

## Findings with no fix
None in scope.

## Waiting for the owner
None.
