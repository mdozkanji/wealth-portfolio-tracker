# Architecture and trade-offs

## 1. Scope: what I built, what I skipped, why

The brief says to choose a subset and defend it. I picked the three capabilities that share one
problem, *who may see and change money-related data, and how that data stays trustworthy*:

1. **Data and permission modeling.** The core of a finance tool. If this is wrong nothing else matters.
2. **Async external data.** A tracker with stale prices is useless, and a cron plus a few HTTP calls is cheap in Odoo.
3. **Real time and UI.** "Insight at a glance": a dashboard that updates when a household member records a trade.

**Skipped on purpose**

* **Embedded AI agent.** An agent multiplies the attack surface (every tool is another path to data and mutation) and
  needs real evaluation to be trustworthy. With limited time I preferred a solid permission core over a
  demo-quality agent on top of a weak one. Section 8 explains how I would add it safely.
* **Non-US stocks and currency conversion for stocks.** Finnhub's free plan quotes US stocks in USD, so other portfolio currencies are flagged instead of mispriced.
* **Currency conversion.** Needs dated FX rates and a policy for historical cost. Portfolios use one currency; the dashboard never adds different currencies together.
* **Realized P&L, fees, dividends, tax lots.** Accounting-grade features; the ledger shape allows them later.

## 2. Why Odoo 18 Community

Odoo already provides what the brief asks me to build or choose: an ORM on PostgreSQL (transactions), ACLs plus
record rules (authorization **below** the API, so every route is covered), `ir.cron`, a websocket bus, a
chatter for notes, and a client framework (OWL). The cost is that it is a heavier framework than a tiny
Next.js app and its extension model has to be learned. I judged that acceptable because *non-bypassable access
control across every route* is a grading criterion, and Odoo enforces rules in the ORM, not in controllers
I could forget to protect.

## 3. Data model

```
portfolio.portfolio ──< portfolio.member (user, role)        who may do what, per portfolio
        │
        └──< portfolio.asset ──< portfolio.transaction        append-only ledger
              (type, price, derived position)                  (buy, sell, transfer_in, transfer_out)
```

* **The ledger is the source of truth.** An asset's `quantity` and `cost_basis` are stored computed fields
  derived by replaying its transactions (weighted-average cost, `models/ledger.py`). Nobody types a quantity.
* **Portfolio totals** derive from assets. Value = quantity x price, P&L = value - cost basis.
* **Prices** come from CoinGecko (crypto), Finnhub (stocks), from a manual edit, or (first valuation only) from the latest transaction price,
  so a new asset is not shown as worth 0. Each price records its source and time.
* Quantities and prices are stored with 8 decimals (sub-cent coins), money fields use currency rounding.
  Views display fewer digits for readability.

## 4. Security model

Two layers, and **a user's power comes only from layer 2**:

1. **Coarse (global).** One group, *Wealth Portfolio User*, given to every internal user. It opens the app and
   sets the maximum CRUD per model (`ir.model.access.csv`). The ledger grants **no** write or delete to anyone.
2. **Fine (per portfolio).** The `role` stored in `portfolio.member`. Record rules (`security/security.xml`)
   look it up, one rule per operation, because Odoo ORs rules of the same group and operation.

Roles are per portfolio on purpose. The same person can be Owner of one portfolio and Viewer of another.
(A global Editor group, as in the first version, lets an editor of A write to B, see `AUDIT.md`.)

| Action | Owner | Editor | Viewer | Not a member |
|---|---|---|---|---|
| See portfolio, assets, ledger, roster | yes | yes | yes | no |
| Add assets, edit price, record / reverse ledger entries, sync prices | yes | yes | no | no |
| Rename, archive, delete portfolio; manage members | yes | no | no | no |
| Delete an asset (only if it has no ledger) | yes | no | no | no |
| Edit or delete a ledger entry | nobody | nobody | nobody | nobody |
| Post notes in the chatter | yes | yes | yes | no |

**Defences beyond record rules**

* *Derived fields cannot be forged.* `write`/`create` on asset and portfolio reject `quantity`, `cost_basis`,
  values and price metadata unless the caller is server code (`env.su`). Odoo otherwise allows writing a
  readonly stored computed field over JSON-RPC.
* *Ledger immutability twice.* ACL has no write/unlink, and `write`/`unlink` raise even for `sudo()`.
  Corrections are reversal entries (one per entry, never reversing a reversal).
* *No overselling, even back-dated.* After each insert the ledger is replayed and any negative running balance is refused.
* *Last owner protected.* A portfolio cannot lose its last owner.
* *Archive, not delete.* Portfolios and assets with ledger entries cannot be deleted (also enforced by `ON DELETE RESTRICT`).

**Where `sudo()` is used, and why** (each is a deliberate, narrow exception)

| Place | Reason |
|---|---|
| Creating the creator's owner membership | The member rule only lets owners add members; a new portfolio has none yet |
| Reading the member list to notify | Editors and viewers trigger pings but may not read everyone's roster |
| Ledger sequence number | Avoids depending on end users' access to `ir.sequence` |
| Price-sync writes | The right to sync is checked first (button: `check_access('write')`, cron: system) |
| First valuation from a transaction | A viewer can never reach this; it runs after an editor's authorized insert |
| Owner-count and "has assets" checks | The check must see all rows regardless of the caller |

## 5. Async processing

`ir.cron` runs every 15 minutes as OdooBot. It collects **all** crypto assets with a CoinGecko ID and asks for
all coins and currencies in **one** `/simple/price` request (chunked at 100 ids). This matters for the free
rate limit and for blocking a worker for one request instead of N.

Failure handling: network error, timeout, HTTP 429 or any non-200, invalid JSON and unexpected shapes raise
`CoinGeckoError`; a missing, negative or non-numeric quote is skipped and flagged. In every failure case
**existing prices stay untouched**, the problem is stored in `sync_error`, and the dashboard shows a warning.
The HTTP client is a plain module so it can be mocked.

**Stocks (Finnhub)** use the *same* cron entry point (`_cron_sync_prices` runs both providers, then sends one bus ping per portfolio).
Differences that shaped the design:

* Finnhub has no batch endpoint, so it is one request per ticker. A cap of 50 symbols per run (free plan: 60 calls/minute),
  least recently updated first, keeps a run inside the limit while still refreshing every stock over time.
* The API key is a **system parameter**, not code: the repository is public, and a committed key is a leaked key.
  It is sent in the `X-Finnhub-Token` header so it cannot appear in URLs, access logs or tracebacks.
* Finnhub answers HTTP 200 with all-zero fields for an unknown symbol, so a price of 0 is treated as "no quote", never stored.
* Quotes are in USD. Rather than guess an exchange rate, portfolios in other currencies are flagged on the asset.
* A rate limit (429) or network error stops the run, keeps the prices already fetched, and reports the rest.

Limit: the cron runs inside an Odoo worker, not a separate queue. Fine at this scale; a real job queue
(`queue_job` or an external worker) is the next step if sync volume grows.

## 6. Real time

```
server  --bus ping {portfolio_id}-->  each member's browser  --RPC get_dashboard_data-->  server (record rules apply)
```

The ping contains **no financial data** and goes to each member's own partner channel, which Odoo authorizes.
The browser then *pulls* through the normal ORM. I rejected a per-portfolio channel such as
`portfolio_channel_<id>`: Odoo 18's websocket accepts client-supplied channel names unchecked (see
`bus/models/ir_websocket.py`, `_build_bus_channel_list`), so any user could subscribe to a guessed id and
receive that portfolio's values. Push-then-pull keeps authorization in one place.

Pings are sent once per portfolio per operation (a 20-asset sync produces one ping per member, not 20),
and the client debounces reloads by 400 ms. Presence indicators were skipped.

## 7. Contract integrity (no end-to-end static types)

The brief asks how a non-TypeScript stack guarantees integrity across boundaries. Here:

* The **model definition is the contract.** Field types, `required`, selections and SQL `CHECK` / `UNIQUE`
  constraints exist once, in Python, and are enforced by the database and ORM for *every* caller (UI, RPC, cron, tests).
* The only hand-written API is `get_dashboard_data`, covered by tests that assert its shape and values.
* The OWL client is plain JavaScript. The risk is a drift between that payload and the template; the
  browser test exercises it. A TypeScript layer or a JSON schema for the payload would be the next improvement.

## 8. How an AI agent would be added without weakening this

Every tool function would run `self.env(user=<the requesting user>)` and call the same ORM methods as the UI,
**never `sudo()`**. Record rules, ACLs, the derived-field guard and ledger immutability then apply to the agent
exactly as to the person, so a viewer's agent cannot mutate anything. Write tools would create ledger entries
only (never edit positions), which makes agent mistakes reversible. Tool arguments would be validated before
the ORM call, and the agent would be given only the portfolios the user can already `search`.

## 9. Known limitations

* CoinGecko and Finnhub were verified with mocked responses and error paths; the live endpoints were not reachable from my
  build environment, so run one sync of each on your machine (README) to confirm.
* Prices live in the portfolio currency; CoinGecko is asked for that currency, and Finnhub stocks require a USD portfolio.
* Weighted-average cost is the only costing method. Reversing a *sell* re-buys at the sell price, not the old average.
* A user deleted from Odoo disappears from rosters; if they were the only owner the portfolio becomes ownerless.
* Dashboard is not paginated (fine for households, not for thousands of portfolios).
