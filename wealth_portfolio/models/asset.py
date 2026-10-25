import logging
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from . import coingecko, finnhub
from .ledger import replay

_logger = logging.getLogger(__name__)

FINNHUB_MAX_PER_RUN = 50  # free plan allows 60 calls/minute; leave headroom

ASSET_TYPES = [
    ('stock', 'Stock'),
    ('crypto', 'Crypto'),
    ('real_estate', 'Real Estate'),
    ('cash', 'Cash'),
]


class PortfolioAsset(models.Model):
    _name = 'portfolio.asset'
    _description = 'Portfolio Asset'
    _order = 'portfolio_id, asset_type, name, id'

    name = fields.Char(string='Asset', required=True)
    portfolio_id = fields.Many2one(
        'portfolio.portfolio', required=True, ondelete='restrict', index=True)
    currency_id = fields.Many2one(related='portfolio_id.currency_id', store=True, readonly=True)
    asset_type = fields.Selection(ASSET_TYPES, required=True, default='stock')
    coingecko_id = fields.Char(
        string='CoinGecko ID',
        help="Coin identifier on CoinGecko (e.g. 'bitcoin', 'ethereum'). Crypto assets with an ID "
             "are priced automatically.")

    symbol = fields.Char(
        string='Ticker Symbol',
        help="Stock ticker used for automatic pricing via Finnhub (e.g. AAPL). "
             "Only US-listed stocks in USD portfolios are supported.")

    transaction_ids = fields.One2many('portfolio.transaction', 'asset_id', string='Transactions')

    # --- derived from the ledger ------------------------------------------
    quantity = fields.Float(digits=(18, 8), compute='_compute_position', store=True)
    cost_basis = fields.Monetary(compute='_compute_position', store=True)
    avg_cost = fields.Float(string='Average Cost', digits=(18, 8), compute='_compute_avg_cost')

    # --- market data -------------------------------------------------------
    current_price = fields.Float(digits=(18, 8), default=0.0)
    price_source = fields.Selection(
        [('ledger', 'Last transaction'), ('manual', 'Manual'), ('coingecko', 'CoinGecko'), ('finnhub', 'Finnhub')], readonly=True)
    price_updated_at = fields.Datetime(readonly=True)
    sync_error = fields.Char(readonly=True)

    # --- derived from position x price --------------------------------------
    current_value = fields.Monetary(compute='_compute_value', store=True)
    unrealized_pnl = fields.Monetary(string='Unrealized P&L', compute='_compute_value', store=True)

    my_role = fields.Selection(related='portfolio_id.my_role')

    _sql_constraints = [
        ('price_non_negative', 'CHECK(current_price >= 0)', 'The price cannot be negative.'),
    ]

    # Fields only the ledger / the price sync may change.
    _PROTECTED_FIELDS = {
        'quantity', 'cost_basis', 'current_value', 'unrealized_pnl',
        'price_source', 'price_updated_at', 'sync_error', 'currency_id',
    }

    # ---------------------------------------------------------------- compute
    @api.depends('transaction_ids.tx_type', 'transaction_ids.quantity',
                 'transaction_ids.price', 'transaction_ids.date')
    def _compute_position(self):
        for asset in self:
            quantity, cost, _lowest = replay(asset.transaction_ids)
            asset.quantity = quantity
            asset.cost_basis = cost

    @api.depends('quantity', 'cost_basis')
    def _compute_avg_cost(self):
        for asset in self:
            asset.avg_cost = asset.cost_basis / asset.quantity if asset.quantity else 0.0

    @api.depends('quantity', 'current_price', 'cost_basis')
    def _compute_value(self):
        for asset in self:
            asset.current_value = asset.quantity * asset.current_price
            asset.unrealized_pnl = asset.current_value - asset.cost_basis

    # ------------------------------------------------------------ constraints
    @api.constrains('asset_type', 'current_price')
    def _check_cash_price(self):
        for asset in self:
            if asset.asset_type == 'cash' and abs(asset.current_price - 1.0) > 1e-9:
                raise ValidationError(_("Cash is always priced at 1 (quantity is the amount)."))

    def _check_ledger_balance(self):
        """Replay each asset's ledger and refuse any state that sells what is not held."""
        for asset in self:
            _quantity, _cost, lowest = replay(asset.transaction_ids)
            if lowest < -1e-9:
                raise ValidationError(_(
                    "This would leave '%s' with a negative quantity at some point in time. "
                    "You cannot sell or transfer out more than you hold.", asset.name))

    # -------------------------------------------------------------------- ORM
    def _guard_protected(self, vals):
        if not self.env.su and self._PROTECTED_FIELDS & set(vals):
            raise AccessError(_(
                "Quantity, cost and value come from the transaction ledger and cannot be edited directly."))

    @api.model_create_multi
    def create(self, vals_list):
        now = fields.Datetime.now()
        for vals in vals_list:
            self._guard_protected(vals)
            if vals.get('asset_type') == 'cash':
                vals['current_price'] = 1.0  # the form sends 0.0 by default, so force it
            if vals.get('current_price') and not self.env.su:
                vals['price_source'] = 'manual'
                vals['price_updated_at'] = now
        assets = super().create(vals_list)
        return assets

    def write(self, vals):
        self._guard_protected(vals)
        if 'portfolio_id' in vals and any(a.portfolio_id.id != vals['portfolio_id'] for a in self):
            raise UserError(_("An asset cannot be moved to another portfolio: its ledger belongs to it."))
        if vals.get('asset_type') == 'cash' and 'current_price' not in vals:
            vals = dict(vals, current_price=1.0)
        if 'current_price' in vals and not self.env.su:
            vals = dict(vals, price_source='manual', price_updated_at=fields.Datetime.now())
        res = super().write(vals)
        if 'current_price' in vals and not self.env.context.get('wp_skip_notify'):
            self.portfolio_id._notify_members()
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_has_ledger(self):
        if any(a.sudo().transaction_ids for a in self):
            raise UserError(_("An asset with ledger entries cannot be deleted."))

    def _apply_ledger_price(self, transactions):
        """Give a never-priced asset a first valuation from its latest transaction price.

        Without this a freshly bought house would show a value of 0 until someone
        typed a price. Later prices (manual or CoinGecko) always override it.
        """
        for asset in self.sudo().filtered(lambda a: not a.price_source):
            latest = transactions.filtered(lambda t: t.asset_id == asset).sorted(
                key=lambda t: (t.date, t.id))[-1:]
            if latest and latest.price > 0:
                asset.with_context(wp_skip_notify=True).write({
                    'current_price': latest.price,
                    'price_source': 'ledger',
                    'price_updated_at': fields.Datetime.now(),
                })

    # ------------------------------------------------------------ price sync
    @api.model
    def _cron_sync_prices(self):
        """Entry point of the scheduled action. Runs as OdooBot, so no record rules apply.

        One job, two providers: CoinGecko for crypto, Finnhub for stocks.
        """
        self.search([('asset_type', 'in', ('crypto', 'stock'))])._sync_prices()

    def action_sync_prices(self):
        """Button: sync the selected assets. Needs write access on every one of them."""
        self.check_access('write')
        result = self._sync_prices()
        if not result['targets']:
            message, kind = _("Nothing to update: crypto needs a CoinGecko ID, stocks need a ticker symbol."), 'warning'
        elif result['error']:
            message, kind = _("Price sync problem: %s", result['error']), 'danger'
        else:
            message = _("%(updated)s price(s) updated, %(missing)s without a quote.",
                        updated=result['updated'], missing=result['missing'])
            kind = 'warning' if result['missing'] else 'success'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': _("Price sync"), 'message': message, 'type': kind, 'sticky': False},
        }

    def _sync_prices(self):
        """Run both providers, then send ONE bus ping per affected portfolio."""
        crypto = self._sync_crypto_prices()
        stocks = self._sync_stock_prices()
        targets = self.sudo().filtered(lambda a: a._is_sync_target())
        targets.portfolio_id._notify_members()
        errors = [e for e in (crypto['error'], stocks['error']) if e]
        return {
            'targets': crypto['targets'] + stocks['targets'],
            'updated': crypto['updated'] + stocks['updated'],
            'missing': crypto['missing'] + stocks['missing'],
            'error': '; '.join(errors) or False,
        }

    def _is_sync_target(self):
        self.ensure_one()
        if self.asset_type == 'crypto':
            return bool((self.coingecko_id or '').strip())
        if self.asset_type == 'stock':
            return bool((self.symbol or '').strip())
        return False

    def _sync_stock_prices(self):
        """Fetch stock quotes from Finnhub (one request per symbol).

        * The API key is a system parameter (`wealth_portfolio.finnhub_api_key`),
          never stored in code or in the repository.
        * Finnhub quotes US stocks in USD and Odoo Community keeps no reliable FX
          rates, so only USD portfolios are synced; others are flagged, not mispriced.
        * At most FINNHUB_MAX_PER_RUN symbols per run (free plan: 60 calls/minute),
          least recently updated first, so a large book is covered over several runs.
        * A failure never overwrites an existing price; it only sets `sync_error`.
        """
        assets = self.sudo().filtered(lambda a: a.asset_type == 'stock' and (a.symbol or '').strip())
        summary = {'targets': len(assets), 'updated': 0, 'missing': 0, 'error': False}
        if not assets:
            return summary
        quiet = assets.with_context(wp_skip_notify=True)

        api_key = self.env['ir.config_parameter'].sudo().get_param('wealth_portfolio.finnhub_api_key')
        if not api_key:
            message = _("Finnhub API key missing: set the system parameter wealth_portfolio.finnhub_api_key")
            quiet.write({'sync_error': message})
            summary['error'] = message
            return summary

        eligible = quiet.filtered(lambda a: a.currency_id.name == 'USD')
        for asset in quiet - eligible:
            asset.write({'sync_error': _(
                "Finnhub quotes US stocks in USD, but this portfolio uses %s", asset.currency_id.name)})
            summary['missing'] += 1
        eligible = eligible.sorted(key=lambda a: (a.price_updated_at or datetime(1970, 1, 1), a.id))
        eligible = eligible[:FINNHUB_MAX_PER_RUN]
        if not eligible:
            return summary

        try:
            prices, errors = finnhub.fetch_quotes(
                symbols={a.symbol.strip().upper() for a in eligible},
                api_key=api_key,
                base_url=self.env['ir.config_parameter'].sudo().get_param(
                    'wealth_portfolio.finnhub_url', finnhub.DEFAULT_BASE_URL),
            )
        except finnhub.FinnhubError as exc:
            _logger.warning("Finnhub sync failed: %s", exc)
            eligible.write({'sync_error': str(exc)[:200]})
            summary['error'] = str(exc)
            return summary

        now = fields.Datetime.now()
        for asset in eligible:
            symbol = asset.symbol.strip().upper()
            if symbol in prices:
                asset.write({
                    'current_price': prices[symbol],
                    'price_source': 'finnhub',
                    'price_updated_at': now,
                    'sync_error': False,
                })
                summary['updated'] += 1
            else:
                asset.write({'sync_error': f"{symbol}: {errors.get(symbol, 'no quote')}"[:200]})
                summary['missing'] += 1
        return summary

    def _sync_crypto_prices(self):
        """Fetch prices for all given crypto assets with ONE batched request.

        Writes happen with sudo: the caller's right to trigger a sync was already
        checked (button) or is implied (cron). A failed request never touches
        existing prices; it only records `sync_error` so the UI can show it.
        """
        assets = self.sudo().filtered(
            lambda a: a.asset_type == 'crypto' and (a.coingecko_id or '').strip())
        summary = {'targets': len(assets), 'updated': 0, 'missing': 0, 'error': False}
        if not assets:
            return summary

        params = self.env['ir.config_parameter'].sudo()
        try:
            prices = coingecko.fetch_simple_prices(
                coin_ids={a.coingecko_id.strip().lower() for a in assets},
                vs_currencies={a.currency_id.name.lower() for a in assets},
                base_url=params.get_param('wealth_portfolio.coingecko_url', coingecko.DEFAULT_BASE_URL),
                api_key=params.get_param('wealth_portfolio.coingecko_api_key') or None,
            )
        except coingecko.CoinGeckoError as exc:
            _logger.warning("CoinGecko sync failed: %s", exc)
            assets.with_context(wp_skip_notify=True).write({'sync_error': str(exc)[:200]})
            summary['error'] = str(exc)
            return summary

        now = fields.Datetime.now()
        for asset in assets.with_context(wp_skip_notify=True):
            coin = asset.coingecko_id.strip().lower()
            currency = asset.currency_id.name.lower()
            quote = (prices.get(coin) or {}).get(currency)
            if isinstance(quote, bool) or not isinstance(quote, (int, float)) or quote < 0:
                asset.write({'sync_error': _("No %(cur)s quote for '%(coin)s'", cur=currency.upper(), coin=coin)})
                summary['missing'] += 1
                continue
            asset.write({
                'current_price': quote,
                'price_source': 'coingecko',
                'price_updated_at': now,
                'sync_error': False,
            })
            summary['updated'] += 1
        return summary  # the caller (_sync_prices) sends one ping per portfolio

    # ---------------------------------------------------------------- actions
    def action_new_transaction(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("New transaction"),
            'res_model': 'portfolio.transaction',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_asset_id': self.id},
        }
