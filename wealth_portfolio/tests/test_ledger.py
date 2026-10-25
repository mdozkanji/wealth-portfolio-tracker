from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import WealthCase


@tagged('post_install', '-at_install', 'wealth_portfolio')
class TestLedger(WealthCase):

    def test_position_is_derived_with_weighted_average_cost(self):
        self.buy(self.btc, 1, 100, date='2026-01-01 10:00:00')
        self.buy(self.btc, 1, 200, date='2026-01-02 10:00:00')
        self.assertAlmostEqual(self.btc.quantity, 2)
        self.assertAlmostEqual(self.btc.cost_basis, 300)
        self.assertAlmostEqual(self.btc.avg_cost, 150)
        self.buy(self.btc, 1, 400, tx_type='sell', date='2026-01-03 10:00:00')
        self.assertAlmostEqual(self.btc.quantity, 1)
        self.assertAlmostEqual(self.btc.cost_basis, 150)       # sold unit leaves at avg cost
        self.btc.current_price = 500
        self.assertAlmostEqual(self.btc.current_value, 500)
        self.assertAlmostEqual(self.btc.unrealized_pnl, 350)

    def test_portfolio_totals_follow_assets(self):
        self.buy(self.btc, 2, 100)
        self.buy(self.flat, 1, 1000)
        self.assertAlmostEqual(self.family.total_cost, 1200)
        self.assertAlmostEqual(self.family.total_value, 1200)   # first valuation = last tx price
        self.btc.with_user(self.alice).write({'current_price': 150})
        self.assertAlmostEqual(self.family.total_value, 1300)
        self.assertAlmostEqual(self.family.unrealized_pnl, 100)

    def test_overselling_is_refused(self):
        self.buy(self.btc, 1, 100)
        with self.assertRaises(ValidationError):
            self.buy(self.btc, 2, 100, tx_type='sell')

    def test_backdated_entry_cannot_make_history_negative(self):
        self.buy(self.btc, 1, 100, date='2026-02-01 10:00:00')
        with self.assertRaises(ValidationError):
            self.buy(self.btc, 1, 100, tx_type='sell', date='2026-01-01 10:00:00')

    def test_entries_are_immutable_for_everyone(self):
        tx = self.buy(self.btc, 1, 100)
        with self.assertRaises(UserError):
            tx.with_user(self.alice).write({'price': 1})
        with self.assertRaises(UserError):
            tx.sudo().write({'price': 1})                       # even server code
        with self.assertRaises(UserError):
            tx.sudo().unlink()
        # ...and the ACL itself grants no write/unlink (defence in depth)
        with self.assertRaises(AccessError):
            tx.with_user(self.alice).check_access('write')
        with self.assertRaises(AccessError):
            tx.with_user(self.alice).check_access('unlink')

    def test_asset_with_ledger_cannot_be_deleted(self):
        self.buy(self.btc, 1, 100)
        with self.assertRaises(UserError):
            self.btc.with_user(self.alice).unlink()

    def test_reversal_cancels_the_entry_and_is_one_shot(self):
        tx = self.buy(self.btc, 2, 100)
        tx.with_user(self.alice).action_reverse()
        self.assertTrue(tx.is_reversed)
        self.assertAlmostEqual(self.btc.quantity, 0)
        self.assertAlmostEqual(self.btc.cost_basis, 0)
        self.assertEqual(len(self.env['portfolio.transaction'].search([('asset_id', '=', self.btc.id)])), 2)
        with self.assertRaises(UserError):
            tx.with_user(self.alice).action_reverse()
        reversal = tx.reversal_ids
        with self.assertRaises(UserError):
            reversal.with_user(self.alice).action_reverse()

    def test_viewer_cannot_reverse(self):
        tx = self.buy(self.btc, 1, 100)
        with self.assertRaises(AccessError):
            tx.with_user(self.carol).action_reverse()

    def test_reversal_refused_if_it_would_oversell(self):
        buy = self.buy(self.btc, 1, 100)
        self.buy(self.btc, 1, 100, tx_type='sell')
        with self.assertRaises(ValidationError):
            buy.with_user(self.alice).action_reverse()

    def test_input_validation(self):
        with self.assertRaises(Exception), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self.buy(self.btc, 0, 100)
        with self.assertRaises(Exception), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self.buy(self.btc, 1, -5)

    def test_cash_is_priced_at_one(self):
        cash = self.env['portfolio.asset'].with_user(self.alice).create(
            {'name': 'Savings', 'portfolio_id': self.family.id, 'asset_type': 'cash', 'current_price': 0})
        self.assertEqual(cash.current_price, 1.0)
        with self.assertRaises(ValidationError):
            self.buy(cash, 100, 2)
        self.buy(cash, 500, 1)
        self.assertAlmostEqual(cash.current_value, 500)
        self.assertAlmostEqual(cash.unrealized_pnl, 0)

    def test_sequence_and_first_valuation(self):
        tx = self.buy(self.flat, 1, 250000)
        self.assertRegex(tx.name, r'^TX\d{6}$')
        self.assertEqual(self.flat.price_source, 'ledger')
        self.assertEqual(self.flat.current_price, 250000)
        self.flat.with_user(self.alice).write({'current_price': 260000})
        self.assertEqual(self.flat.price_source, 'manual')
        self.buy(self.flat, 1, 1, tx_type='buy')                # must NOT overwrite a manual valuation
        self.assertEqual(self.flat.current_price, 260000)

    def test_currency_locked_once_assets_exist(self):
        eur = self.env.ref('base.EUR')
        with self.assertRaises(UserError):
            self.family.with_user(self.alice).write({'currency_id': eur.id})
