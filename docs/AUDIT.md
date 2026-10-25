# Audit of the first version

The first version was installed on a real Odoo 18 server and probed with scripted attacks.
Everything marked **reproduced** was executed, not inferred from reading.

## Verdict

The skeleton was sound (right models, right idea of record rules, `<list>` views already Odoo 18 style) but the
parts the assessment grades hardest, *explicit non-bypassable access control* and *data integrity*, did not hold,
and several README claims were not true. I rebuilt the models, security, sync, real time and UI; the names
(`portfolio.portfolio`, `portfolio.asset`, `portfolio.transaction`) and overall plan are kept.

## Findings

### Critical: security

| # | Finding | Evidence |
|---|---|---|
| S1 | Record rule exists only on `portfolio.portfolio`. Assets and transactions have **no rule**, so any user in the Viewer group reads every asset and ledger entry of every portfolio. | Reproduced: bob (member of nothing) read Alice's private asset (`Secret`, value 1000.0) and transaction. |
| S2 | Roles are **global groups**. Being in the global *Editor* group plus merely listed as a *viewer* of a portfolio grants write on it, and on every asset and transaction in the system. | Reproduced: eve (Editor group, only in `viewer_ids`) renamed the portfolio and made herself owner; wrote to Alice's asset; injected an asset into her portfolio. |
| S3 | One rule serves all operations; the Editor/Viewer lists never limit what a *listed* user can do. Owners/editors can rewrite `editor_ids`, `viewer_ids`, `owner_id` (privilege escalation). | Reproduced (S2). |
| S4 | Real-time channel `portfolio_channel_<id>` is a guessable string. Odoo 18 websocket subscribes to client-supplied channels without checks, and the payload contained `total_value`. | Source: `addons/bus/models/ir_websocket.py`, `_build_bus_channel_list` appends client channels as given. |

### Critical: data integrity

| # | Finding | Evidence |
|---|---|---|
| D1 | The "immutable ledger" is not immutable: Editors have `perm_write`, Owners `perm_unlink`, and there is no override. Asset deletion also cascades and wipes its ledger. | Reproduced: ledger entry edited and deleted. |
| D2 | The ledger is **not connected** to anything. Positions (`quantity`, `purchase_price`) are typed by hand; a sell entry does not change the position. Negative quantities and prices are accepted. | Reproduced: quantity unrelated to entries; `quantity=-5, price=-1` accepted. |
| D3 | There is **no view, action or menu for transactions**, so the ledger cannot be used from the UI at all. `purchase_price` is never shown. | Read of `views/`. |
| D4 | Derived fields (`current_value`, `total_value`) are writable over RPC. | Odoo allows writing readonly stored computed fields. |
| D5 | `Monetary` used for unit prices rounds to 2 decimals, which breaks sub-cent assets. `name` default `'New'` is never replaced by a sequence. | Read of models. |

### High: async and real time

| # | Finding | Evidence |
|---|---|---|
| A1 | Only the asset literally named "bitcoin" can ever be priced; coin id is hardcoded, currency is hardcoded to USD whatever the portfolio currency. One HTTP request per asset. | `asset.py`: `ids=bitcoin&vs_currencies=usd`. |
| A2 | Failures are swallowed (`logger.error`), status codes other than 200 ignored (429 silently), no timestamp, no UI signal. Stocks are `pass`. | `asset.py`. |
| A3 | The bus notification is in `portfolio.write()`, but the cron changes a *price*, which recomputes a stored field via the ORM's internal write path. **Zero bus messages are emitted when prices change**, exactly the case real time is for. | Reproduced: `bus messages emitted: 0` after changing `current_price`. |
| A4 | No client code subscribes to the bus: no `static/`, no OWL. The plan promised an OWL dashboard. | Directory listing. |

### Medium: packaging and honesty

* README claims "an embedded AI assistant with secure tool-calling execution": **not built, and your own plan says it is dropped.** An evaluator who reads this will doubt everything else. Also claims "immutable ledgers" and "instant UI value synchronization", neither true. Remove claims you cannot demonstrate.
* README contains stray `[cite: 1]` markers (copied from an AI tool), a Python code fence around a shell command, and no database or user setup steps.
* `__pycache__/*.pyc` files are in the ZIP. Add a `.gitignore`.
* `web_icon` points to `static/description/icon.png`, which does not exist.
* No tests, no demo data (nobody can see roles working), no groups assigned to anyone by default.
* Manifest `depends` omits `bus` (works through `mail`, but declare what you use).

### What was good

* Right decision to build natively in Odoo and to justify it.
* `<list>` instead of `<tree>`, `<chatter/>`, `invisible="1"` : correct Odoo 18 syntax.
* The three-model decomposition and the idea of an append-only ledger.
* Honest "what this is not" section in the plan.

## Mapping to the plan

| Plan item | First version | Now |
|---|---|---|
| Three roles, per-portfolio access | Global groups + M2M lists, rules only on portfolio | `portfolio.member` roles, rules on all four models, per operation |
| Immutable ledger | Declared only | ACL + `write`/`unlink` overrides + reversal flow + replay validation |
| Asset quantities and valuations | Typed manually | Derived from the ledger, price source and time recorded |
| CoinGecko via cron | Bitcoin only, per asset | One batched request, error handling, per-asset sync status |
| Bus real time | Server-side, not firing, no client | Per-member ping, OWL dashboard that reloads through the ORM |
| OWL dashboard | Absent | Built, with loading, empty, error and refreshing states |
| Tailwind or Bootstrap | Undecided | Bootstrap and SCSS (Odoo's native stack) |
| Drop the AI agent | Dropped, README says otherwise | Dropped, documented, with a safe design for adding it |

## Plan feedback still relevant to your writing

* Remove "I'm in no means a Full-Stack Software Developer". State what you have (Odoo, Python, security background).
* Add a "Deferred and why" list. The brief says what you skip is evaluated.
* Answer the type-safety question from the brief (see `ARCHITECTURE.md` section 7).
* Plan said transaction types `buy, sell, transfer`; the build uses `transfer_in` and `transfer_out`, because a
  single "transfer" has no direction and cannot be replayed. Say that you changed it and why.
