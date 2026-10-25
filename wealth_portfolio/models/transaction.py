from odoo import api, fields, models, _
from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG
from odoo.exceptions import UserError, ValidationError

from .ledger import OPPOSITE

TX_TYPES = [
    ('buy', 'Buy'),
    ('sell', 'Sell'),
    ('transfer_in', 'Transfer In'),
    ('transfer_out', 'Transfer Out'),
]


class PortfolioTransaction(models.Model):
    """Append-only ledger entry.

    Entries are never edited or deleted (see write/unlink and the ACL, which
    grants no write/unlink permission to anyone). A mistake is fixed with a
    *reversal*: a new opposite entry that points at the original.
    """
    _name = 'portfolio.transaction'
    _description = 'Portfolio Ledger Entry'
    _order = 'date desc, id desc'

    name = fields.Char(string='Reference', readonly=True, copy=False, default='New')
    asset_id = fields.Many2one('portfolio.asset', string='Asset', required=True, ondelete='restrict', index=True)
    portfolio_id = fields.Many2one(related='asset_id.portfolio_id', store=True, index=True)
    currency_id = fields.Many2one(related='portfolio_id.currency_id', store=True)
    tx_type = fields.Selection(TX_TYPES, string='Type', required=True, default='buy')
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    quantity = fields.Float(digits=(18, 8), required=True)
    price = fields.Float(
        string='Unit Price', digits=(18, 8), required=True,
        help="Price per unit in the portfolio currency. For transfers: the unit value at transfer time.")
    total_amount = fields.Monetary(compute='_compute_total_amount', store=True)
    note = fields.Char()

    reversal_of_id = fields.Many2one('portfolio.transaction', string='Reversal of', readonly=True,
                                     ondelete='restrict', copy=False)
    reversal_ids = fields.One2many('portfolio.transaction', 'reversal_of_id')
    is_reversed = fields.Boolean(compute='_compute_is_reversed', store=True)

    my_role = fields.Selection(related='portfolio_id.my_role')

    _sql_constraints = [
        ('reversal_unique', 'unique(reversal_of_id)', 'This entry has already been reversed.'),
        ('quantity_positive', 'CHECK(quantity > 0)', 'The quantity must be strictly positive.'),
        ('price_non_negative', 'CHECK(price >= 0)', 'The price cannot be negative.'),
    ]

    # ---------------------------------------------------------------- compute
    @api.depends('quantity', 'price')
    def _compute_total_amount(self):
        for tx in self:
            tx.total_amount = tx.quantity * tx.price

    @api.depends('reversal_ids')
    def _compute_is_reversed(self):
        for tx in self:
            tx.is_reversed = bool(tx.reversal_ids)

    @api.constrains('asset_id', 'price')
    def _check_cash_price(self):
        for tx in self:
            if tx.asset_id.asset_type == 'cash' and abs(tx.price - 1.0) > 1e-9:
                raise ValidationError(_("Cash entries use a unit price of 1 (the quantity is the amount)."))

    @api.onchange('asset_id')
    def _onchange_asset_id(self):
        if self.asset_id.asset_type == 'cash':
            self.price = 1.0
        elif self.asset_id.current_price:
            self.price = self.asset_id.current_price

    # -------------------------------------------------------------------- ORM
    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env['ir.sequence'].sudo()
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = sequence.next_by_code('portfolio.transaction') or 'New'
        transactions = super().create(vals_list)
        assets = transactions.asset_id
        # Refuse overselling, including through back-dated entries.
        assets._check_ledger_balance()
        assets._apply_ledger_price(transactions)
        transactions.portfolio_id._notify_members()
        return transactions

    def write(self, vals):
        raise UserError(_("Ledger entries are immutable. Use 'Reverse' to correct a mistake."))

    def unlink(self):
        if not self.env.context.get(MODULE_UNINSTALL_FLAG):
            raise UserError(_("Ledger entries cannot be deleted. Use 'Reverse' to correct a mistake."))
        return super().unlink()

    # ---------------------------------------------------------------- actions
    def action_reverse(self):
        """Post the opposite entry. The original stays in the ledger, flagged as reversed."""
        reversals = self.browse()
        for tx in self:
            if tx.reversal_of_id:
                raise UserError(_("%s is itself a reversal and cannot be reversed.", tx.name))
            if tx.is_reversed:
                raise UserError(_("%s has already been reversed.", tx.name))
            reversals |= self.create({
                'asset_id': tx.asset_id.id,
                'tx_type': OPPOSITE[tx.tx_type],
                'quantity': tx.quantity,
                'price': tx.price,
                'reversal_of_id': tx.id,
                'note': _("Reversal of %s", tx.name),
            })
        return True
