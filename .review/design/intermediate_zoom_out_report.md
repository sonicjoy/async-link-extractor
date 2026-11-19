# Stage 8: Intermediate Zoom Out

**Target:** `docs/designs/linkx.md`, full file (274 lines, before the fixes below). One reviewer (`egm-zoom-out`), completed. No Blocker; 4 Major, 9 Nit. Claims about asyncio, signal and httpx behaviour were made from memory; the one that changed the design (F12) was then checked by experiment.

## Major

- **F1. The per-host wait sat inside the attempt timeout.** With a delay and many workers on one host, queued workers would time out before sending anything, then retry and push the schedule further back. *Fixed:* the limiter wait now happens before `asyncio.timeout` starts.
- **F2. No mapping from exceptions to error kinds.** A DNS failure would have become `unexpected` and not been retried. *Fixed:* explicit mapping added (timeouts, transport errors and resolver failures to `timeout`/`connect`, and so on).
- **F8. The SIGINT handler's lifetime was unspecified.** An interrupt before workers start did nothing, and a later one cancelled only consumers, which could leave `block`-mode workers waiting forever. *Fixed:* `interrupt()` records that it was called, `run` starts no workers after it, later calls cancel workers and consumers, and a `KeyboardInterrupt` outside the handler's span exits 130.
- **F12. Hand-written redirect following left gaps** (which statuses, missing `Location`, relative targets, the redirect limit). *Fixed:* an experiment on httpx 0.28.1 confirmed that a request event hook runs before every redirect hop and that an exception raised in it propagates unchanged, so httpx follows redirects and the hook does the checks.

## Nit (all fixed)

F3 duplicate detection moved to `emit`; F4 "returns normally" scoped to interrupts; F5 goals updated for the second Ctrl-C; F6 URL prefix test made case-insensitive; F7 duplicate count routed through `cli.py`; F9 exit 130 whenever `interrupt()` was called; F10 a running parse finishes before exit; F11 closed-pipe exit code left to tests; F13 `POLICIES` table kept as the single source for the CLI's choices.

## Length

The reviewer listed cuts worth about 450 words. Most were applied; the document went from about 3,000 words to about 2,700 before the logging and future-work sections were added at the author's request.
