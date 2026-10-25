from odoo import fields, models, _
from odoo.exceptions import ValidationError

from .portfolio import ROLES


class PortfolioMember(models.Model):
    """Who may do what *in a given portfolio*.

    This table is the heart of the permission model: roles are per portfolio,
    not global. The same user can be Owner of one portfolio and Viewer of
    another. Record rules on every model look this table up.
    """
    _name = 'portfolio.member'
    _description = 'Portfolio Member'
    _order = 'portfolio_id, role, id'

    portfolio_id = fields.Many2one('portfolio.portfolio', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one(
        'res.users', string='User', required=True, ondelete='cascade', index=True,
        domain=[('share', '=', False)])
    role = fields.Selection(ROLES, required=True, default='viewer')

    _sql_constraints = [
        ('portfolio_user_unique', 'unique(portfolio_id, user_id)',
         'This user is already a member of the portfolio.'),
    ]

    def _check_portfolios_keep_an_owner(self, portfolios):
        # sudo: the check must see *all* members, whatever the caller may read.
        for portfolio in portfolios.sudo().exists():
            if not portfolio.member_ids.filtered(lambda m: m.role == 'owner'):
                raise ValidationError(_("Portfolio '%s' must keep at least one owner.", portfolio.name))

    def write(self, vals):
        portfolios = self.portfolio_id
        res = super().write(vals)
        if 'role' in vals or 'portfolio_id' in vals:
            self._check_portfolios_keep_an_owner(portfolios | self.portfolio_id)
        return res

    def unlink(self):
        portfolios = self.portfolio_id
        res = super().unlink()
        self._check_portfolios_keep_an_owner(portfolios)
        return res
