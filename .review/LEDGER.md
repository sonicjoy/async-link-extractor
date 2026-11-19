# Review ledger

One row per review pass. Reports are in the folder named in each row.

| Date | Target | Pass | Result | Report |
| --- | --- | --- | --- | --- |
| 2026-11-19 | Concept | 7-lens brainstorm review | 6 contradictions found; record shape, single sink, worker pool adopted | [docs/designs/linkx-brief.md](../docs/designs/linkx-brief.md) |
| 2026-11-19 | Design | Stage 5, initial zoom out | 0 Blocker, 7 Major, 8 Nit; catch-all guard and second Ctrl-C fixed; two author decisions kept | [design/zoom_out_report.md](design/zoom_out_report.md) |
| 2026-11-19 | Design | Stage 6, comprehension | 0 Blocker, 4 Major, 9 Nit; failure paths and record fields specified | [design/comprehension_report.md](design/comprehension_report.md) |
| 2026-11-19 | Design | Stage 7, critic | 0 Blocker, 6 Major, 17 Nit; drain on late Ctrl-C, audit gap, memory bound fixed | [design/critic_report.md](design/critic_report.md) |
| 2026-11-19 | Design | Stage 9, implementation readiness | 1 Blocker, 13 Major, 11 Nit; non-public address policy decided, test seams named | [design/implementation_readiness_report.md](design/implementation_readiness_report.md) |
| 2026-11-19 | Design | Stage 8, intermediate zoom out | 0 Blocker, 4 Major, 9 Nit; redirect checks moved to an httpx hook after an experiment | [design/intermediate_zoom_out_report.md](design/intermediate_zoom_out_report.md) |

Stages 7 and 9 ran in parallel because they review different halves of the document; stage 8 ran after both.

## Decisions kept against reviewer advice

| Decision | Reviewer position | Why it stands |
| --- | --- | --- |
| Per-host delay in the core | Defer to a design note | Politeness towards target hosts matters for any real fetcher; it is off by default and costs one small class |
| `--consumers M`, default 1 | Fix one consumer | Shows the queue works many-to-many; pays off on free-threaded builds; the README gives measured numbers |
| `drop-oldest` as the default | Weak reason for a finite list | The requirement asks for trimming the oldest entries; `block` is one flag away |

## Behaviour confirmed by experiment rather than from memory

| Question | Result (Python 3.13.18 and 3.15.2, Windows) |
| --- | --- |
| Does `asyncio.run` drain queued results on Ctrl-C? | No: queued results are lost unless the cancel is absorbed |
| What does a second Ctrl-C do under `asyncio.run`? | Raises `KeyboardInterrupt` and cancels the consumers mid-drain |
| Does an explicit SIGINT handler give a two-stage stop? | Yes: first press drains, second stops the drain, no `KeyboardInterrupt` |
| Does an httpx request hook run on every redirect hop? | Yes (httpx 0.28.1), and an exception raised in it propagates unchanged |
