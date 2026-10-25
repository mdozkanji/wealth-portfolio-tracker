# Wealth Portfolio Tracker (Odoo 18 Community)

Shared multi-asset portfolios (stocks, crypto, real estate, cash) with **per-portfolio roles**,
an **append-only ledger**, a scheduled **CoinGecko price sync**, and a **live dashboard**.

Built for the Talent Syria technical assessment. Design decisions and trade-offs are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## What is built (and what is not)

| Capability | Status |
|---|---|
| Data and permission modeling: portfolios, assets, ledger, Owner / Editor / Viewer **per portfolio** | Built, tested |
| Async processing: one `ir.cron` every 15 min: crypto via CoinGecko (one batched request), US stocks via Finnhub | Built, tested with mocked APIs |
| Real time: bus ping to each member, dashboard re-reads through the ORM | Built, tested in a browser |
| UI: OWL dashboard (loading, empty, error and refreshing states), list/form views, read-only states per role | Built |
| Embedded AI agent | **Deliberately not built.** See "Scope" in the architecture doc |
| Stock price feed | Built for US-listed stocks in USD portfolios. Real estate and cash are priced manually |
| Multi-currency conversion | Not built. All amounts in a portfolio use the portfolio currency |
| Realized P&L, fees, dividends | Not built |

## Run it

Requirements: Python 3.10 or newer (built and tested on 3.12), PostgreSQL (tested on 16), the [Odoo 18.0 source](https://github.com/odoo/odoo/tree/18.0) with its `requirements.txt` installed.

```bash
git clone <this repo> wealth-portfolio-tracker

# create the database WITH demo data (it creates the demo users alice / bob / carol)
python odoo-bin \
  --addons-path=odoo/addons,/path/to/wealth-portfolio-tracker \
  -d wealth -i wealth_portfolio \
  --db_user=odoo --db_password=odoo
```

Open <http://localhost:8069> and log in with **alice / alice**, **bob / bob** or **carol / carol**.

| User | Role in *Family Wealth* | Role in *Alice Private* |
|---|---|---|
| alice | Owner | Owner |
| bob | Editor | no access |
| carol | Viewer | no access |

**Don't see alice, bob and carol under Settings, Users?** Odoo only loads demo data when the *database* was created
with demo data enabled (the web database manager leaves "Load demonstration data" unticked by default). Either create a
fresh database with demo data (`-d newname -i wealth_portfolio` on the command line does that), or load it into your
existing database:

```bash
python odoo-bin shell -c odoo.conf -d wealth < /path/to/wealth-portfolio-tracker/scripts/load_demo.py
```

It prints `Demo data loaded.` and is safe to run twice. Or skip demo data and create three internal users yourself,
then add them on a portfolio's **Members** tab with the roles you want.

The built-in `admin` account intentionally sees **no** portfolios unless it is a member:
record rules apply to it like to anyone else. Only server code running as superuser bypasses them.

To try the live update: open the dashboard as alice, then as bob record a transaction in another browser.
Alice's numbers change without a reload.

### Run the tests

```bash
python odoo-bin --addons-path=odoo/addons,/path/to/wealth-portfolio-tracker \
  -d wealth_test -i wealth_portfolio --test-tags /wealth_portfolio --stop-after-init \
  --http-port=8099
```

50 tests: permission attacks (cross-portfolio reads and writes, role escalation, forged derived
fields), ledger arithmetic and immutability, price-sync failure modes for both providers, dashboard payloads.

### CoinGecko

Crypto assets with a **CoinGecko ID** (for example `bitcoin`) are priced by the scheduled action
*Wealth Portfolio: sync crypto prices*. The server needs outbound HTTPS to `api.coingecko.com`.
Optional system parameters (Settings, Technical, System Parameters):

* `wealth_portfolio.coingecko_api_key`: demo API key, sent as `x-cg-demo-api-key`
* `wealth_portfolio.coingecko_url`: override the base URL (default `https://api.coingecko.com/api/v3`)

A failed sync never overwrites a price. It records the problem on the asset and the dashboard shows a warning.

### Finnhub (stock prices)

Stocks with a **Ticker Symbol** (for example `AAPL`) are priced by the same scheduled action. Setup:

1. Get a free key at <https://finnhub.io>.
2. In Odoo: Settings, Technical, System Parameters, create `wealth_portfolio.finnhub_api_key` with the key as value.
   **Never commit the key to Git**; it lives only in the database.
3. Optional: `wealth_portfolio.finnhub_url` overrides the base URL (default `https://finnhub.io/api/v1`).

Behavior and limits:

* Finnhub has no batch endpoint: one request per symbol, at most **50 symbols per run** (the free plan allows 60 calls/minute).
  When there are more, the least recently updated are served first, so every stock is refreshed over successive runs.
* Free plan covers **US-listed stocks, quoted in USD**. Portfolios in another currency are **flagged, not synced**
  (Odoo Community keeps no reliable FX rates, and a USD price shown as EUR would be wrong).
* Unknown symbols (Finnhub answers 200 with zeros), a missing key, a rejected key (401), a plan restriction (403),
  rate limiting (429) and network errors are all reported on the asset. An existing price is never overwritten by a failure.
* Outside market hours the last close is returned.

To verify the key from a terminal (it travels in a header, not the URL):

```bash
curl -s -H "X-Finnhub-Token: $FINNHUB_KEY" "https://finnhub.io/api/v1/quote?symbol=AAPL"
# expect {"c": <price>, ...}; "c": 0 means unknown symbol
```

### Real-time note

The bus works in Odoo's default threaded mode for development. In production, run with
`--workers` and let your reverse proxy forward `/websocket` to the gevent port, as for any Odoo deployment.

## Layout

```
wealth_portfolio/
  models/        portfolio, member (roles), asset, transaction (ledger), ledger (pure maths), coingecko + finnhub (HTTP clients)
  security/      one coarse group + per-operation record rules driven by portfolio.member
  views/ data/   views, menus, sequence, cron, demo data
  scripts/       load_demo.py: loads demo data into an existing database
  static/src/    OWL dashboard
  tests/         security, ledger, price sync, dashboard
docs/            architecture, audit of the first version, video guide
```
