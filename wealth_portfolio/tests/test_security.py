from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import WealthCase


@tagged('post_install', '-at_install', 'wealth_portfolio')
class TestSecurity(WealthCase):
    """Every attack below SUCCEEDED against the first version of the module."""

    # ---- visibility -----------------------------------------------------
    def test_non_member_sees_nothing(self):
        for model in ('portfolio.portfolio', 'portfolio.asset', 'portfolio.transaction', 'portfolio.member'):
            self.assertFalse(self.env[model].with_user(self.dave).search([]), model)

    def test_children_are_protected_not_only_the_portfolio(self):
        """Original bug: a viewer could read ALL assets/transactions of everybody."""
        self.buy(self.btc, 1, 50000)
        carol = self.carol
        self.assertEqual(self.env['portfolio.asset'].with_user(carol).search([('portfolio_id', '=', self.private.id)]).ids, [])
        self.assertEqual(self.env['portfolio.transaction'].with_user(carol).search([]).mapped('portfolio_id'), self.family)
        with self.assertRaises(AccessError):
            self.secret.with_user(carol).read(['name'])
        with self.assertRaises(AccessError):
            self.tx_secret.with_user(carol).read(['price'])

    # ---- roles are per portfolio ---------------------------------------
    def test_viewer_is_read_only(self):
        with self.assertRaises(AccessError):
            self.btc.with_user(self.carol).write({'name': 'x'})
        with self.assertRaises(AccessError):
            self.buy(self.btc, 1, 1, user=self.carol)
        with self.assertRaises(AccessError):
            self.env['portfolio.asset'].with_user(self.carol).create({'name': 'n', 'portfolio_id': self.family.id})

    def test_editor_can_record_but_not_administer(self):
        self.buy(self.btc, 1, 50000, user=self.bob)            # allowed
        self.btc.with_user(self.bob).write({'current_price': 51000})  # allowed
        with self.assertRaises(AccessError):
            self.family.with_user(self.bob).write({'name': 'Mine now'})
        with self.assertRaises(AccessError):
            self.env['portfolio.member'].with_user(self.bob).create(
                {'portfolio_id': self.family.id, 'user_id': self.dave.id, 'role': 'owner'})
        with self.assertRaises(AccessError):
            self.family.with_user(self.bob).unlink()
        with self.assertRaises(AccessError):
            self.btc.with_user(self.bob).unlink()

    def test_role_in_one_portfolio_gives_nothing_in_another(self):
        """Original bug: global Editor group let you write everywhere you were merely listed."""
        # bob is editor in Family only; make him viewer in a second portfolio owned by dave
        other = self.env['portfolio.portfolio'].with_user(self.dave).create({'name': 'Dave'})
        self.env['portfolio.member'].with_user(self.dave).create(
            {'portfolio_id': other.id, 'user_id': self.bob.id, 'role': 'viewer'})
        asset = self.env['portfolio.asset'].with_user(self.dave).create({'name': 'D', 'portfolio_id': other.id})
        self.assertEqual(other.with_user(self.bob).my_role, 'viewer')
        with self.assertRaises(AccessError):
            asset.with_user(self.bob).write({'name': 'hacked'})
        with self.assertRaises(AccessError):
            self.env['portfolio.asset'].with_user(self.bob).create({'name': 'inj', 'portfolio_id': other.id})
        with self.assertRaises(AccessError):
            other.with_user(self.bob).write({'name': 'hacked'})

    def test_cannot_inject_into_foreign_portfolio(self):
        with self.assertRaises(AccessError):
            self.env['portfolio.asset'].with_user(self.dave).create({'name': 'inj', 'portfolio_id': self.family.id})
        with self.assertRaises(AccessError):
            self.buy(self.secret, 1, 1, user=self.dave)

    def test_owner_manages_roster_and_last_owner_is_protected(self):
        member = self.env['portfolio.member'].with_user(self.alice)
        carol_row = member.search([('portfolio_id', '=', self.family.id), ('user_id', '=', self.carol.id)])
        carol_row.write({'role': 'editor'})
        self.assertEqual(self.family.with_user(self.carol).my_role, 'editor')
        alice_row = member.search([('portfolio_id', '=', self.family.id), ('user_id', '=', self.alice.id)])
        with self.assertRaises(ValidationError):
            alice_row.write({'role': 'viewer'})
        with self.assertRaises(ValidationError):
            alice_row.unlink()

    # ---- derived data cannot be forged ----------------------------------
    def test_ledger_derived_fields_cannot_be_written_over_rpc(self):
        for vals in ({'quantity': 1e9}, {'cost_basis': 1}, {'current_value': 1e9}, {'price_source': 'coingecko'}):
            with self.assertRaises(AccessError, msg=str(vals)):
                self.btc.with_user(self.alice).write(vals)
        with self.assertRaises(AccessError):
            self.family.with_user(self.alice).write({'total_value': 1e12})
        with self.assertRaises(AccessError):
            self.env['portfolio.asset'].with_user(self.alice).create(
                {'name': 'x', 'portfolio_id': self.family.id, 'quantity': 5})

    def test_asset_cannot_be_moved_between_portfolios(self):
        other = self.env['portfolio.portfolio'].with_user(self.alice).create({'name': 'Other'})
        with self.assertRaises(UserError):
            self.btc.with_user(self.alice).write({'portfolio_id': other.id})

    def test_creator_becomes_owner_and_my_role_reports_it(self):
        self.assertEqual(self.family.with_user(self.alice).my_role, 'owner')
        self.assertEqual(self.family.with_user(self.bob).my_role, 'editor')
        self.assertEqual(self.family.with_user(self.carol).my_role, 'viewer')

    def test_portfolio_with_ledger_cannot_be_deleted(self):
        with self.assertRaises(UserError):
            self.private.with_user(self.alice).unlink()
