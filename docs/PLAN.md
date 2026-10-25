# Wealth Portfolio Tracker: Technical Plan and Architecture

Date Created: October 23, 2026 11:51 AM
Last Updated: October 25, 2026 9:33 AM

**Author:** Mofeed Dozkanji

**Platform:** Odoo 18 Community Edition, module `wealth_portfolio`

**Status: I**mplemented; see "Verification" for what was tested and what was not

---

# 1. Goal and the One Idea Behind the Design

From my understanding, a household needs to see what it owns across stocks, crypto, real estate and cash, let several people work on the same portfolios with different privileges, and trust the numbers.

The brief says to choose a subset and defend it. I picked the four capabilities that share one problem, *who may see and change money-related data, and how that data stays trustworthy*:

1. **Data & Permission Modeling.** The core of a finance tool. If this is wrong nothing else matters.
2. **Async Processing & External Data.** A tracker with fixed prices is useless, and a cron plus one HTTP call is cheap in Odoo.
3. **Real-Time & 4. UI/UX Craftsmanship.** "Insight at a glance": a dashboard that updates when a household member records a trade.

**Skipped on purpose:**

- **Embedded AI agent.** An agent multiplies the attack surface (every tool is another path to data and mutation) and needs real evaluation to be trustworthy. With limited time I preferred a solid permission core over a demo-quality agent on top of a weak one. Section 8 explains how I would add it safely.
- **Currency conversion.** Needs dated FX rates and a policy for historical cost. Portfolios use one currency; the dashboard never adds different currencies together.
- **Realized P&L, fees, dividends, tax lots.** Accounting-grade features; the ledger shape allows them later.

---

# 2. Why Odoo 18 Community

I have over a year of Odoo experience and six years of Python, and Odoo already provides what the brief asks me to build or pick. Odoo provides a robust foundation for financial applications due to its core Object-Relational Mapping (ORM) layer, native PostgreSQL transactional integrity, role-based security framework (`ir.model.access`), automated background worker queues (`ir.cron`), and built-in WebSocket streaming infrastructure (`bus.bus`). Hence, I’ll approach this project and design the **Wealth Portfolio Tracker** as a native Odoo 18 Community module (`wealth_portfolio`).

By using Odoo’s native capabilities and features, we avoid unnecessary framework overhead without compromising on the end-product’s requirements. The tradeoff that I accept, however, is that Odoo is a heavy framework, and I’m extending a large system rather that designing a stack from scratch. I judged that worth it because "explicit, non-bypassable access control" is a grading criterion, and Odoo enforces rules in the ORM, not in controllers I could forget to protect.

UI uses Odoo's native Bootstrap and SCSS pipeline.

---

# 3. Directory Structure & Data Model

Every Odoo module follows an MVC-like structure, separating ORM from UI, and has advanced structural components that I’ll mention later in the document. The following is the directory structure for `wealth_portfolio`:

```markup
wealth_portfolio/
├── __init__.py
├── __manifest__.py
├── data/
│   └── cron_data.xml
│   └── demo.xml
│   └── sequence_data.xml
├── models/
│   ├── __init__.py
│   ├── asset.py
│   ├── coingecko.py
│   ├── funnhub.py
│   ├── ledger.py
│   ├── member.py
│   ├── portfolio.py
│   └── transaction.py
├── security/
│   ├── ir.model.access.csv
│   └── security.xml
├── static/
│   ├── src/dashboard/
│   │   ├── dashboard.js
│   │   ├── dashboard.scss
│   │   └── dashboard.xml
│   └── description/icon.png
├── views/
│   └── asset_views.xml
│   └── menus.xml
│   └── portfolio_views.xml
│   └── transaction_views.xml
└── README.md
```

- **`portfolio.portfolio`**: name, currency, derived totals. Created by any user, who becomes its owner.
- **`portfolio.member`**: user and role (`owner`, `editor`, `viewer`) in one portfolio.
- **`portfolio.asset`**: type (`stock`, `crypto`, `real_estate`, `cash`), price with source and timestamp, and a *derived* position (quantity, cost basis).
- **`portfolio.transaction`**: `buy`, `sell`, `transfer_in`, `transfer_out`. I split transfer into two directions because a single "transfer" cannot be replayed.

**Few things worth highlighting:**

- **The ledger is the source of truth.** Quantity and cost basis are never typed: they are computed by replaying an asset's entries with weighted-average cost. This removes the classic bug where a stored position and the history disagree.
- **Portfolio totals** derive from assets. Value = quantity x price, P&L = value - cost basis.
- **Prices** come from CoinGecko, Finnhub, a manual edit, or (first valuation only) from the latest transaction price, so a new asset is not shown as worth 0. Each price records its source and time.
- Quantities and prices are stored with 8 decimals (sub-cent coins), money fields use currency rounding. Views display fewer digits for readability.

---

# 4. Security Design

Two layers; a user's power comes only from the second:

1. **Coarse.** One global group, *Wealth Portfolio User*, for every internal user. It opens the app and caps CRUD per model.
2. **Fine, per portfolio.** The role in `portfolio.member`. Record rules on all four models, **one rule per operation** (Odoo ORs rules of the same group and operation, so a combined rule would leak).

<aside>
💡

My first design used three global groups (`Portfolio Owner`, `Portfolio Editor`, `Portfolio Viewer`). I dropped it: a global `Portfolio Editor` group lets someone who edits portfolio A write to B. Roles must belong to the (user, portfolio) pair.

</aside>

|  | Owner | Editor | Viewer |
| --- | --- | --- | --- |
| Read portfolio, assets, ledger, members | yes | yes | yes |
| Add assets, set prices, append or reverse ledger entries, sync prices | yes | yes | no |
| Rename, archive, delete portfolio; manage members | yes | no | no |
| Edit or delete a ledger entry | nobody | nobody | nobody |

**Security beyond record rules:**

- *Derived fields cannot be forged.* `write`/`create` on asset and portfolio reject `quantity`, `cost_basis`, values and price metadata unless the caller is server code (`env.su`). Odoo otherwise allows writing a readonly stored computed field over JSON-RPC.
- *Ledger immutability twice.* ACL has no write/unlink, and `write`/`unlink` raise even for `sudo()`. Corrections are reversal entries (one per entry, never reversing a reversal).
- *No overselling, even back-dated.* After each insert the ledger is replayed and any negative running balance is refused.
- *Last owner protected.* A portfolio cannot lose its last owner.
- *Archive, not delete.* Portfolios and assets with ledger entries cannot be deleted (also enforced by `ON DELETE RESTRICT`).

**Where `sudo()` is used, and why** (each is a deliberate, narrow exception):

| Place | Reason |
| --- | --- |
| Creating the creator's owner membership | The member rule only lets owners add members; a new portfolio has none yet |
| Reading the member list to notify | Editors and viewers trigger pings but may not read everyone's roster |
| Ledger sequence number | Avoids depending on end users' access to `ir.sequence` |
| Price-sync writes | The right to sync is checked first (button: `check_access('write')`, cron: system) |
| First valuation from a transaction | A viewer can never reach this; it runs after an editor's authorized insert |
| Owner-count and "has assets" checks | The check must see all rows regardless of the caller |

---

# 5. Async Processing

This is one of the most important features of a portfolio tracker. Luckily, Odoo has its own background worker threads (`ir.cron`) that I’ll use to gathers all crypto assets with a CoinGecko id and requests all coins and currencies in **one** `/simple/price` call every 15 minutes (chunked at 100 ids). This matters for the free rate limit and for blocking a worker for one request instead of N.

**Failure handling:** network error, timeout, HTTP 429 (rate limit exceeded) or any non-200, invalid JSON and unexpected shapes raise `CoinGeckoError`; a missing, negative or non-numeric quote is skipped and flagged. In every failure case **existing prices stay untouched**, the problem is stored in `sync_error`, and the dashboard shows a warning. The HTTP client is a plain module so it can be mocked.

**Stocks (Finnhub)** use the *same* cron entry point (`_cron_sync_prices` runs both providers, then sends one bus ping per portfolio). Differences that shaped the design:

- Finnhub has no batch endpoint, so it is one request per ticker. A cap of 50 symbols per run (free plan: 60 calls/minute), least recently updated first, keeps a run inside the limit while still refreshing every stock over time.
- The API key is a **system parameter**, not code: the repository is public, and a committed key is a leaked key. It is sent in the `X-Finnhub-Token` header so it cannot appear in URLs, access logs or tracebacks.
- Finnhub answers HTTP 200 with all-zero fields for an unknown symbol, so a price of 0 is treated as "no quote", never stored.
- Quotes are in USD. Rather than guess an exchange rate, portfolios in other currencies are flagged on the asset.
- A rate limit (429) or network error stops the run, keeps the prices already fetched, and reports the rest.

<aside>
💡

**Limit:** the cron runs inside an Odoo worker, not a separate queue. Fine at this scale; a real job queue (`queue_job` or an external worker) is the next step if sync volume grows.

</aside>

---

# 6. Real-Time

Odoo has its own native WebSocket support via the internal `bus` service, which I’ll tap into to push live valuation changes and collaborative portfolio updates instantly to connected clients without the need for any external tools. The server sends each member a tiny bus message containing only the portfolio id; the browser then re-reads through the ORM.

The ping contains **no financial data** and goes to each member's own partner channel, which Odoo authorizes. The browser then *pulls* through the normal ORM. I rejected a per-portfolio channel such as `portfolio_channel_<id>`: Odoo 18's websocket accepts client-supplied channel names unchecked, so any user could subscribe to a guessed id and receive that portfolio's values. Push-then-pull keeps authorization in one place.

Pings are sent once per portfolio per operation (a 20-asset sync produces one ping per member, not 20), and the client debounces reloads by 400 ms.

---

# 7. UI/UX Craftsmanship

An OWL dashboard (net worth per currency, per-portfolio value, unrealized P&L with sign and arrow, allocation bar with a text legend so color is never the only signal), plus standard list and form views. States handled: loading skeleton, empty, error with retry, refreshing, read-only forms for viewers.

---

# 8. Contract Integrity

The brief asks how a non-TypeScript stack guarantees integrity across boundaries. Here:

- The **model definition is the contract.** Field types, `required`, selections and SQL `CHECK` / `UNIQUE` constraints exist once, in Python, and are enforced by the database and ORM for *every* caller (UI, RPC, cron, tests).
- The only hand-written API is `get_dashboard_data`, covered by tests that assert its shape and values.
- The OWL client is plain JavaScript. The risk is a drift between that payload and the template; the browser test exercises it. A TypeScript layer or a JSON schema for the payload would be the next improvement.

---

# 9. Verification

- **36 automated tests** on Odoo 18: cross-portfolio reads and writes, role escalation, forged derived fields, ledger arithmetic and immutability, price-sync failure modes, dashboard data.
- I **reintroduced the bugs of my first version one at a time** (missing child rules, editable ledger, forgeable fields, global roles) and confirmed the tests fail each time.
- **Browser run as three users:** one user records a trade and another's open dashboard updates without reload; a viewer is refused when calling the API directly.
- **Not verified:** the live CoinGecko and Finnhub APIs (only mocked responses and real network failures were exercised).

---

# 10. Known Limitations

- Prices live in the portfolio currency; CoinGecko is asked for that currency, and Finnhub stocks require a USD portfolio.
- Weighted-average cost is the only costing method. Reversing a *sell* re-buys at the sell price, not the old average.
- A user deleted from Odoo disappears from rosters; if they were the only owner the portfolio becomes ownerless.