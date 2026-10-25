# Video guide (10 to 15 minutes)

The brief says they want to see that you understand **every line and every decision**. This guide is a map for
*your* explanation. Do not read it aloud: open each file, say in your own words what it does and why, and be ready for
"what happens if...?" questions. Practice the demo once end to end before recording.

## Suggested timeline

| Min | Section | Show |
|---|---|---|
| 0:00 | **Scope and prioritization** | README table. Built: permissions, async prices, real time + UI. Skipped: AI agent, stock feed, FX, realized P&L. Say *why* (see `ARCHITECTURE.md` section 1). |
| 1:30 | **Stack trade-offs** | Why Odoo: rules enforced in the ORM below every route, cron, bus, OWL included. Cost: heavier framework, learning curve. Why not Next.js here. Why Bootstrap/SCSS and not Tailwind (Odoo's native asset pipeline). |
| 3:00 | **Data model** | `models/ledger.py` (30 lines, the whole costing logic), then `asset.py::_compute_position`. Ledger is the source of truth; positions are derived. |
| 5:00 | **Permissions and security boundaries** | Demo: alice, bob, carol dashboards side by side. Open `security/security.xml`: two layers, one rule per operation, why roles live in `portfolio.member`. Then run the failing-attack tests (`test_security.py`). Mention the `sudo()` table. |
| 8:30 | **Integrity** | Try to edit a ledger entry (read-only), oversell (error), reverse an entry. Show `transaction.py` `write`/`unlink`. |
| 10:00 | **Async and real time** | `coingecko.py`, `asset.py::_sync_crypto_prices` (one batched request, failure keeps old prices). Live demo: bob records a trade, alice's dashboard changes. Explain push-then-pull and why no custom channel. |
| 12:30 | **AI agent: not built** | What the brief asks (tools, streaming, permissions in agent context) and your answer: skipped to protect the permission core; how you would add it (`ARCHITECTURE.md` section 8: tools run as the user, never `sudo`, write tools only append ledger entries). |
| 13:30 | **Agentic execution** | Be accurate and specific (below). |
| 14:30 | **Limits and next steps** | `ARCHITECTURE.md` section 9. |

## Agentic execution: say what actually happened

They ask how you *directed* AI tools. Describe your real process, including that this version was reviewed and
rebuilt with Claude after you had a first version. Concrete things you can truthfully point to:

* You wrote the plan and chose the stack and scope yourself.
* The first version was audited against the brief; the audit found real defects (`AUDIT.md`), proven with scripted attacks
  rather than opinions.
* The rewrite is verified by tests that fail when the original bugs are reintroduced (you can demonstrate: comment out a
  record rule, run the tests, watch them fail).
* What you checked yourself. Do this before recording, and say only what is true.

## Questions to be ready for

1. *Why not give editors write on the portfolio header?* Least privilege; membership changes are owner-only.
2. *Can an Odoo admin read everything?* No, the admin sees no portfolios unless a member. Only server `sudo()` bypasses rules.
3. *What stops someone writing `quantity` over JSON-RPC?* The `_guard_protected` check in `asset.py`; test `test_ledger_derived_fields_cannot_be_written_over_rpc`.
4. *Why a ping and not the data?* Odoo accepts any client-chosen channel; keeping payloads empty and pulling through the ORM keeps one authorization path.
5. *Why weighted-average cost?* Simple, replayable from the ledger, no lot tracking needed. Limitation: reversing a sell.
6. *What if CoinGecko is down or rate-limits?* Old prices stay, `sync_error` is set, dashboard warns, next run retries.
7. *What would break at 10,000 portfolios?* Dashboard is unpaginated; cron is single-worker; replay is O(n) per insert.
8. *How do you guarantee contracts without TypeScript?* `ARCHITECTURE.md` section 7.
9. *Why `transfer_in` and `transfer_out`?* A single "transfer" has no direction and cannot be replayed.

## Before you submit

* Push to a Git repository (the brief asks for a repo link, not a ZIP) and confirm the README steps work on a clean machine.
* Run one real CoinGecko sync: set an asset's CoinGecko ID to `bitcoin` and press *Sync prices*. That path was only tested with mocks.
* Record the video, upload as unlisted YouTube / Drive / Loom, reply to the email with both links.
* Deadline: Sunday 25 October 2026, end of day Damascus time (about 4:59 PM Sunday US Eastern).
