from odoo.tests import tagged

from .common import WealthCase


@tagged('post_install', '-at_install', 'wealth_portfolio')
class TestDashboardData(WealthCase):

    def test_each_user_gets_only_their_portfolios(self):
        self.buy(self.btc, 1, 100)
        Portfolio = self.env['portfolio.portfolio']
        self.assertEqual({p['name'] for p in Portfolio.with_user(self.alice).get_dashboard_data()}, {'Family', 'Private'})
        self.assertEqual({p['name'] for p in Portfolio.with_user(self.carol).get_dashboard_data()}, {'Family'})
        self.assertEqual(Portfolio.with_user(self.dave).get_dashboard_data(), [])

    def test_payload_values(self):
        self.buy(self.btc, 2, 100)
        self.btc.with_user(self.alice).write({'current_price': 150})
        self.buy(self.flat, 1, 300)
        data = {p['name']: p for p in self.env['portfolio.portfolio'].with_user(self.bob).get_dashboard_data()}
        family = data['Family']
        self.assertEqual(family['role'], 'editor')
        self.assertAlmostEqual(family['total_value'], 600)
        self.assertAlmostEqual(family['pnl'], 100)
        self.assertAlmostEqual(family['pnl_pct'], 100 / 500 * 100)
        self.assertAlmostEqual(sum(a['pct'] for a in family['allocation']), 100)
        self.assertEqual({a['type'] for a in family['allocation']}, {'crypto', 'real_estate'})

    def test_empty_portfolio_has_no_division_by_zero(self):
        family = self.env['portfolio.portfolio'].with_user(self.alice).get_dashboard_data()
        row = next(p for p in family if p['name'] == 'Family')
        self.assertIsNone(row['pnl_pct'])
        self.assertEqual(row['allocation'], [])
