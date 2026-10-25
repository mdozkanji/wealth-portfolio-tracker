# Wealth Portfolio Tracker: Technical Plan and Architecture

**Author:** Mofeed Dozkanji  
**Platform:** Odoo 18 Community Edition, module `wealth_portfolio`  
**Status:** implemented; see "Verification" for what was tested and what was not

---

## 1. Goal and the one idea behind the design

A household needs to see what it owns across stocks, crypto, real estate and cash, let several people work on the
same portfolios with different powers, and trust the numbers.

Everything below follows from one question: **who may see or change money-related data, and how does that data
stay trustworthy?** Features that do not help answer it (an AI agent, FX conversion, a stock feed) were deferred
on purpose. The brief says what I skip is evaluated, so section 7 gives my reasons.

## 2. Scope

| Capability from the brief | Decision | Why |
|---|---|---|
| Data and permission modeling | **Build, core** | If authorization or the ledger is wrong, nothing else matters |
| Async processing and external data | **Build** | A tracker with stale prices is useless; in Odoo it costs one cron and one HTTP call |
| Real time and UI | **Build** | "Insight at a glance": the dashboard must update when a household member records a trade |
| Embedded AI agent | **Defer** | Every tool is another path to data; I would rather ship a solid permission core than an agent on a weak one (section 6) |

## 3. Why Odoo 18 Community

I have over a year of Odoo experience and six years of Python, and Odoo already provides what the brief asks me to
build or pick: an ORM on PostgreSQL (transactions), ACLs plus **record rules** that are enforced in the ORM *below* every route (UI,
RPC, cron, tests), `ir.cron`, a websocket bus, a chatter for notes, and OWL for the client.

The trade-off I accept: it is a heavier framework than a small Next.js app, and I am extending a large system
rather than designing a stack from scratch. I judged that worth it because "explicit, non-bypassable access control"
is a grading criterion, and rules in the ORM cannot be forgotten on a single endpoint.

UI uses Odoo's native Bootstrap and SCSS pipeline, not Tailwind (Tailwind does not fit Odoo's asset bundles without extra tooling).

## 4. Data model

```
portfolio.portfolio ──< portfolio.member (user, role)      who may do what, per portfolio
        └──< portfolio.asset ──< portfolio.transaction      append-only ledger
```

* **`portfolio.portfolio`**: name, currency, derived totals. Created by any user, who becomes its owner.
* **`portfolio.member`**: user and role (`owner`, `editor`, `viewer`) in one portfolio.
* **`portfolio.asset`**: type (`stock`, `crypto`, `real_estate`, `cash`), price with source and timestamp, and a
  *derived* position (quantity, cost basis).
* **`portfolio.transaction`**: `buy`, `sell`, `transfer_in`, `transfer_out`. I split transfer into two directions
  because a single "transfer" cannot be replayed.

**The ledger is the source of truth.** Quantity and cost basis are never typed: they are computed by replaying an
asset's entries with weighted-average cost. This removes the classic bug where a stored position and the history disagree.

## 5. Security design

Two layers; a user's power comes only from the second.

1. **Coarse.** One global group, *Wealth Portfolio User*, for every internal user. It opens the app and caps CRUD per model.
2. **Fine, per portfolio.** The role in `portfolio.member`. Record rules on all four models, **one rule per operation**
   (Odoo ORs rules of the same group and operation, so a combined rule would leak).

My first design used three global groups. I dropped it: a global Editor group lets someone who edits portfolio A
write to B. Roles must belong to the (user, portfolio) pair.

| | Owner | Editor | Viewer |
|---|---|---|---|
| Read portfolio, assets, ledger, members | yes | yes | yes |
| Add assets, set prices, append or reverse ledger entries, sync prices | yes | yes | no |
| Rename, archive, delete portfolio; manage members | yes | no | no |
| Edit or delete a ledger entry | nobody | nobody | nobody |

Defences beyond record rules:

* **Derived fields cannot be forged.** Odoo lets a client write a readonly stored computed field over RPC, so
  `write`/`create` reject `quantity`, `cost_basis`, values and price metadata unless the caller is server code.
* **Immutable ledger, twice.** The ACL grants no write or delete, and the ORM methods raise even for `sudo()`.
  Fixes are *reversal entries*.
* **No overselling.** After each insert the ledger is replayed; any negative running balance is refused, including back-dated entries.
* **Last owner protected.** A portfolio cannot end up without an owner.
* **`sudo()` is narrow and listed** (creator's owner membership, reading the roster to notify, sequence number,
  price-sync writes after the right to sync was checked, integrity checks that must see all rows).

## 6. Async processing, real time and the agent

**Async.** An `ir.cron` every 15 minutes gathers all crypto assets with a CoinGecko id and requests all coins and currencies in **one**
`/simple/price` call (free tier has a strict rate limit). Network errors, timeouts, HTTP 429, bad JSON and missing quotes never overwrite
an existing price: the problem is stored on the asset and shown on the dashboard. Stocks are priced by the same job through Finnhub (one request per ticker, at most 50 per run, API key kept in a system parameter and never in the repository, USD portfolios only). Real estate and cash are priced manually.

**Real time.** The server sends each member a tiny bus message containing only the portfolio id; the browser then re-reads through the ORM.
I rejected a per-portfolio channel because Odoo 18's websocket accepts client-chosen channel names without checks, so a guessable id would leak values.
Push-then-pull keeps authorization in one place. Pings are sent once per portfolio per operation, and the client debounces reloads.

**UI.** An OWL dashboard (net worth per currency, per-portfolio value, unrealized P&L with sign and arrow, allocation bar with a text legend so colour is never the only signal),
plus standard list and form views. States handled: loading skeleton, empty, error with retry, refreshing, read-only forms for viewers.

**Agent (deferred, designed).** If added, every tool would run as the requesting user (`env(user=...)`), never `sudo()`,
and call the same ORM methods as the UI, so record rules, the derived-field guard and ledger immutability apply to the agent exactly as to the person.
Write tools would only append ledger entries, which makes mistakes reversible. Tools would validate arguments first and see only portfolios the user can already search.

## 7. Deferred and why

* **AI agent:** reasons in section 2 and 6.
* **Non-US stocks and stock FX:** Finnhub's free plan quotes US stocks in USD; other portfolio currencies are flagged, not mispriced.
* **FX conversion:** needs dated rates and a historical-cost policy. A portfolio uses one currency; the dashboard never adds different currencies.
* **Realized P&L, fees, dividends, tax lots:** accounting-grade; the ledger shape allows them later.
* **Presence indicators and separate job queue:** unnecessary at household scale.

## 8. Contract integrity without end-to-end static types

* The **model definition is the contract**: field types, `required`, selections and SQL `CHECK`/`UNIQUE` constraints exist once and are enforced by the database and ORM for every caller.
* The only hand-written API, the dashboard payload, has tests for its shape and values.
* The OWL client is plain JavaScript, so drift between payload and template is the residual risk; a browser test covers it. A TypeScript layer would be the next step.

## 9. Verification

* **50 automated tests** on Odoo 18: cross-portfolio reads and writes, role escalation, forged derived fields, ledger arithmetic and immutability, price-sync failure modes, dashboard data.
* I **reintroduced the bugs of my first version one at a time** (missing child rules, editable ledger, forgeable fields, global roles) and confirmed the tests fail each time.
* **Browser run as three users:** one user records a trade and another's open dashboard updates without reload; a viewer is refused when calling the API directly.
* **Not verified:** the live CoinGecko and Finnhub APIs (only mocked responses and real network failures were exercised).

## 10. Known limitations

Weighted-average cost only (reversing a sell re-buys at the sell price); a deleted sole owner leaves a portfolio ownerless;
dashboard not paginated; sync runs inside an Odoo worker rather than a queue.
