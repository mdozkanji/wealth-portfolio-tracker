from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

BUS_EVENT = 'wealth_portfolio/updated'
ROLES = [('owner', 'Owner'), ('editor', 'Editor'), ('viewer', 'Viewer')]


class Portfolio(models.Model):
    _name = 'portfolio.portfolio'
    _description = 'Wealth Portfolio'
    _inherit = ['mail.thread']
    _order = 'name, id'
    # Every member may post notes in the chatter (default would require write
    # access, which editors deliberately do not have on the portfolio header).
    _mail_post_access = 'read'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', required=True, tracking=True,
        default=lambda self: self.env.company.currency_id,
        help="All asset prices and values in this portfolio are expressed in this currency.")

    member_ids = fields.One2many('portfolio.member', 'portfolio_id', string='Members')
    asset_ids = fields.One2many('portfolio.asset', 'portfolio_id', string='Assets')
    transaction_ids = fields.One2many('portfolio.transaction', 'portfolio_id', string='Transactions')

    # Derived from the assets (which are derived from the ledger): never written by users.
    total_value = fields.Monetary(compute='_compute_totals', store=True, currency_field='currency_id')
    total_cost = fields.Monetary(compute='_compute_totals', store=True, currency_field='currency_id')
    unrealized_pnl = fields.Monetary(
        string='Unrealized P&L', compute='_compute_totals', store=True, currency_field='currency_id')

    # The *current user's* role in this portfolio. Not stored: depends on who is asking.
    my_role = fields.Selection(ROLES, compute='_compute_my_role', string='My Role')

    _PROTECTED_FIELDS = {'total_value', 'total_cost', 'unrealized_pnl'}

    # ------------------------------------------------------------------ compute
    @api.depends('asset_ids.current_value', 'asset_ids.cost_basis')
    def _compute_totals(self):
        for portfolio in self:
            value = sum(portfolio.asset_ids.mapped('current_value'))
            cost = sum(portfolio.asset_ids.mapped('cost_basis'))
            portfolio.total_value = value
            portfolio.total_cost = cost
            portfolio.unrealized_pnl = value - cost

    @api.depends_context('uid')
    @api.depends('member_ids.user_id', 'member_ids.role')
    def _compute_my_role(self):
        uid = self.env.uid
        for portfolio in self:
            membership = portfolio.member_ids.filtered(lambda m: m.user_id.id == uid)[:1]
            portfolio.my_role = membership.role if membership else False

    # ---------------------------------------------------------------------- ORM
    def _guard_protected(self, vals):
        """Derived fields may only be written by trusted server code (sudo)."""
        if not self.env.su and self._PROTECTED_FIELDS & set(vals):
            raise AccessError(_("Portfolio totals are computed from the ledger and cannot be edited."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._guard_protected(vals)
        portfolios = super().create(vals_list)
        # The creator becomes the owner. Done with sudo because the member
        # record rule (rightly) only lets existing owners add members, and a
        # brand-new portfolio has none yet.
        for portfolio in portfolios:
            if not portfolio.sudo().member_ids.filtered(lambda m: m.role == 'owner'):
                self.env['portfolio.member'].sudo().create({
                    'portfolio_id': portfolio.id,
                    'user_id': self.env.user.id,
                    'role': 'owner',
                })
        return portfolios

    def write(self, vals):
        self._guard_protected(vals)
        if 'currency_id' in vals and any(
                p.sudo().asset_ids and p.currency_id.id != vals['currency_id'] for p in self):
            raise UserError(_("The currency cannot be changed once a portfolio contains assets."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_has_assets(self):
        if any(p.sudo().asset_ids for p in self):
            raise UserError(_("A portfolio with assets or ledger entries cannot be deleted. Archive it instead."))

    # ---------------------------------------------------------------- real-time
    def _notify_members(self):
        """Tell every member's browser that this portfolio changed.

        The message carries *no financial data*, only the portfolio id. It is
        sent on each member's own partner channel, which Odoo authorizes
        itself. The client reacts by re-reading through the normal ORM, so
        record rules decide what it may see. (A custom string channel would be
        subscribable by anyone who can guess it: Odoo 18's websocket accepts
        client-supplied channel names as they are.)
        """
        bus = self.env['bus.bus']
        for portfolio in self:
            for partner in portfolio.sudo().member_ids.user_id.partner_id:
                bus._sendone(partner, BUS_EVENT, {'portfolio_id': portfolio.id})

    # ------------------------------------------------------------------ actions
    def action_sync_prices(self):
        return self.asset_ids.action_sync_prices()

    # ---------------------------------------------------------------- dashboard
    @api.model
    def get_dashboard_data(self):
        """Everything the OWL dashboard shows, computed under the caller's rights.

        `search` applies the record rules, so a user only ever receives the
        portfolios (and, through them, assets) they are a member of.
        """
        portfolios = self.search([])
        result = []
        for portfolio in portfolios:
            allocation = {}
            for asset in portfolio.asset_ids:
                allocation[asset.asset_type] = allocation.get(asset.asset_type, 0.0) + asset.current_value
            total = portfolio.total_value
            labels = dict(self.env['portfolio.asset']._fields['asset_type'].selection)
            prices = portfolio.asset_ids.filtered('price_updated_at').mapped('price_updated_at')
            result.append({
                'id': portfolio.id,
                'name': portfolio.name,
                'role': portfolio.my_role,
                'currency_id': portfolio.currency_id.id,
                'currency_name': portfolio.currency_id.name,
                'total_value': total,
                'total_cost': portfolio.total_cost,
                'pnl': portfolio.unrealized_pnl,
                'pnl_pct': (portfolio.unrealized_pnl / portfolio.total_cost * 100.0) if portfolio.total_cost else None,
                'asset_count': len(portfolio.asset_ids),
                'last_price_update': fields.Datetime.to_string(max(prices)) if prices else False,
                'sync_errors': len(portfolio.asset_ids.filtered('sync_error')),
                'allocation': [
                    {
                        'type': asset_type,
                        'label': labels.get(asset_type, asset_type),
                        'value': value,
                        'pct': (value / total * 100.0) if total else 0.0,
                    }
                    for asset_type, value in sorted(allocation.items(), key=lambda kv: -kv[1])
                    if value
                ],
            })
        return result
