from unittest.mock import patch

from odoo.tests import tagged

from ..models import coingecko, finnhub
from .common import WealthCase

FETCH = 'odoo.addons.wealth_portfolio.models.asset.coingecko.fetch_simple_prices'


@tagged('post_install', '-at_install', 'wealth_portfolio')
class TestPriceSync(WealthCase):

    def setUp(self):
        super().setUp()
        self.eth = self.env['portfolio.asset'].with_user(self.alice).create(
            {'name': 'Ether', 'portfolio_id': self.family.id, 'asset_type': 'crypto', 'coingecko_id': ' Ethereum '})
        self.buy(self.btc, 1, 100)
        self.buy(self.eth, 2, 10)

    def test_one_batched_request_for_all_assets(self):
        with patch(FETCH, return_value={'bitcoin': {'usd': 70000}, 'ethereum': {'usd': 2500}}) as fetch:
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertEqual(fetch.call_count, 1)
        kwargs = fetch.call_args.kwargs
        self.assertEqual(kwargs['coin_ids'], {'bitcoin', 'ethereum'})        # normalized
        self.assertEqual(kwargs['vs_currencies'], {self.family.currency_id.name.lower()})
        self.assertEqual(self.btc.current_price, 70000)
        self.assertEqual(self.btc.price_source, 'coingecko')
        self.assertEqual(self.eth.current_price, 2500)
        self.assertFalse(self.btc.sync_error)

    def test_failure_keeps_old_prices_and_records_the_problem(self):
        self.btc.sudo().write({'current_price': 123})
        with patch(FETCH, side_effect=coingecko.CoinGeckoError('rate limited (HTTP 429)')):
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertEqual(self.btc.current_price, 123)
        self.assertIn('429', self.btc.sync_error)

    def test_missing_quote_is_flagged_not_zeroed(self):
        self.btc.sudo().write({'current_price': 123})
        with patch(FETCH, return_value={'ethereum': {'usd': 2500}}):
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertEqual(self.btc.current_price, 123)
        self.assertIn('BITCOIN'.lower(), self.btc.sync_error.lower())
        self.assertEqual(self.eth.current_price, 2500)

    def test_garbage_quotes_are_ignored(self):
        with patch(FETCH, return_value={'bitcoin': {'usd': 'NaN?'}, 'ethereum': {'usd': -3}}):
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertNotEqual(self.btc.price_source, 'coingecko')
        self.assertNotEqual(self.eth.price_source, 'coingecko')

    def test_button_requires_write_access_and_reports_result(self):
        with patch(FETCH, return_value={'bitcoin': {'usd': 1}}):
            result = self.btc.with_user(self.bob).action_sync_prices()   # editor: ok
            self.assertEqual(result['tag'], 'display_notification')
            from odoo.exceptions import AccessError
            with self.assertRaises(AccessError):
                self.btc.with_user(self.carol).action_sync_prices()      # viewer: refused

    def test_assets_without_coingecko_id_are_skipped(self):
        with patch(FETCH) as fetch:
            result = self.flat.with_user(self.alice).action_sync_prices()
        fetch.assert_not_called()
        self.assertEqual(result['params']['type'], 'warning')

    def test_http_client_error_handling(self):
        class Resp:
            def __init__(self, code, payload=None, bad_json=False):
                self.status_code, self._p, self._bad = code, payload, bad_json
            def json(self):
                if self._bad:
                    raise ValueError
                return self._p
        import requests
        def run(resp=None, exc=None):
            with patch('odoo.addons.wealth_portfolio.models.coingecko.requests.get',
                       side_effect=exc, return_value=resp):
                return coingecko.fetch_simple_prices(['bitcoin'], ['usd'])
        self.assertEqual(run(Resp(200, {'bitcoin': {'usd': 1}})), {'bitcoin': {'usd': 1}})
        for bad in (Resp(429), Resp(500), Resp(200, bad_json=True), Resp(200, ['x'])):
            with self.assertRaises(coingecko.CoinGeckoError):
                run(bad)
        with self.assertRaises(coingecko.CoinGeckoError):
            run(exc=requests.Timeout())
        self.assertEqual(coingecko.fetch_simple_prices([], ['usd']), {})

    def test_notifications_carry_no_financial_data_and_go_to_members_only(self):
        bus = type(self.env['bus.bus'])
        with patch.object(bus, '_sendone') as sendone:
            self.buy(self.btc, 1, 100)
        targets = {call.args[0].id for call in sendone.call_args_list}
        self.assertEqual(targets, {u.partner_id.id for u in (self.alice, self.bob, self.carol)})
        self.assertNotIn(self.dave.partner_id.id, targets)
        for call in sendone.call_args_list:
            self.assertEqual(call.args[1], 'wealth_portfolio/updated')
            self.assertEqual(call.args[2], {'portfolio_id': self.family.id})

    def test_sync_notifies_once_per_portfolio_not_per_asset(self):
        bus = type(self.env['bus.bus'])
        with patch(FETCH, return_value={'bitcoin': {'usd': 70000}, 'ethereum': {'usd': 2500}}), \
                patch.object(bus, '_sendone') as sendone:
            self.env['portfolio.asset']._cron_sync_prices()
        mine = [c for c in sendone.call_args_list if c.args[2]['portfolio_id'] == self.family.id]
        self.assertEqual(len(mine), 3)                # 3 members x 1 ping, however many assets changed


FH_FETCH = 'odoo.addons.wealth_portfolio.models.asset.finnhub.fetch_quotes'
FH_GET = 'odoo.addons.wealth_portfolio.models.finnhub.requests.Session.get'


@tagged('post_install', '-at_install', 'wealth_portfolio')
class TestStockSync(WealthCase):

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('wealth_portfolio.finnhub_api_key', 'test-key')
        Asset = self.env['portfolio.asset'].with_user(self.alice)
        self.aapl = Asset.create({'name': 'Apple', 'portfolio_id': self.family.id,
                                  'asset_type': 'stock', 'symbol': ' aapl '})
        self.msft = Asset.create({'name': 'Microsoft', 'portfolio_id': self.family.id,
                                  'asset_type': 'stock', 'symbol': 'MSFT'})
        self.buy(self.aapl, 10, 150)
        self.buy(self.msft, 5, 300)

    def test_cron_prices_stocks_and_normalizes_symbols(self):
        with patch(FH_FETCH, return_value=({'AAPL': 215.5, 'MSFT': 410.0}, {})) as fetch:
            self.env['portfolio.asset']._cron_sync_prices()      # the SAME cron entry point as crypto
        self.assertEqual(fetch.call_args.kwargs['symbols'], {'AAPL', 'MSFT'})   # ' aapl ' -> 'AAPL'
        self.assertEqual(fetch.call_args.kwargs['api_key'], 'test-key')
        self.assertEqual((self.aapl.current_price, self.aapl.price_source), (215.5, 'finnhub'))
        self.assertEqual(self.msft.current_price, 410.0)
        self.assertFalse(self.aapl.sync_error)
        self.assertAlmostEqual(self.aapl.unrealized_pnl, 10 * (215.5 - 150))

    def test_one_job_runs_both_providers_and_pings_once(self):
        bus = type(self.env['bus.bus'])
        with patch(FETCH, return_value={'bitcoin': {'usd': 70000}}), \
                patch(FH_FETCH, return_value=({'AAPL': 215.5, 'MSFT': 410.0}, {})), \
                patch.object(bus, '_sendone') as sendone:
            self.buy(self.btc, 1, 100)
            sendone.reset_mock()
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertEqual(self.btc.price_source, 'coingecko')
        self.assertEqual(self.aapl.price_source, 'finnhub')
        mine = [c for c in sendone.call_args_list if c.args[2]['portfolio_id'] == self.family.id]
        self.assertEqual(len(mine), 3)       # 3 members, one ping, though 3 assets changed

    def test_missing_api_key_is_reported_not_silent(self):
        self.env['ir.config_parameter'].sudo().set_param('wealth_portfolio.finnhub_api_key', '')
        self.aapl.sudo().write({'current_price': 123})
        with patch(FH_FETCH) as fetch:
            self.env['portfolio.asset']._cron_sync_prices()
        fetch.assert_not_called()
        self.assertEqual(self.aapl.current_price, 123)
        self.assertIn('finnhub_api_key', self.aapl.sync_error)

    def test_bad_symbols_are_flagged_and_keep_old_price(self):
        self.msft.sudo().write({'current_price': 111})
        with patch(FH_FETCH, return_value=({'AAPL': 200.0}, {'MSFT': 'no quote (unknown symbol or no trading data)'})):
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertEqual(self.aapl.current_price, 200.0)
        self.assertEqual(self.msft.current_price, 111)
        self.assertIn('MSFT', self.msft.sync_error)

    def test_non_usd_portfolio_is_flagged_not_mispriced(self):
        eur = self.env.ref('base.EUR')
        euro = self.env['portfolio.portfolio'].with_user(self.alice).create({'name': 'Euro', 'currency_id': eur.id})
        sap = self.env['portfolio.asset'].with_user(self.alice).create(
            {'name': 'Foreign', 'portfolio_id': euro.id, 'asset_type': 'stock', 'symbol': 'IBM', 'current_price': 99})
        with patch(FH_FETCH, return_value=({'AAPL': 200.0, 'MSFT': 400.0}, {})) as fetch:
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertNotIn('IBM', fetch.call_args.kwargs['symbols'])
        self.assertEqual(sap.current_price, 99)
        self.assertIn('EUR', sap.sync_error)

    def test_rejected_key_marks_every_stock(self):
        with patch(FH_FETCH, side_effect=finnhub.FinnhubError('Finnhub rejected the API key (HTTP 401)')):
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertIn('401', self.aapl.sync_error)
        self.assertIn('401', self.msft.sync_error)
        self.assertFalse(self.aapl.price_source == 'finnhub')

    def test_per_run_cap_updates_least_recent_first(self):
        from odoo.addons.wealth_portfolio.models import asset as asset_module
        self.aapl.sudo().write({'price_updated_at': '2026-01-01 00:00:00'})
        self.msft.sudo().write({'price_updated_at': '2026-06-01 00:00:00'})
        with patch.object(asset_module, 'FINNHUB_MAX_PER_RUN', 1), \
                patch(FH_FETCH, return_value=({'AAPL': 200.0}, {})) as fetch:
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertEqual(fetch.call_args.kwargs['symbols'], {'AAPL'})     # oldest first
        self.assertEqual(self.msft.price_source, 'ledger')                # untouched this run

    def test_stock_without_symbol_is_not_a_target(self):
        plain = self.env['portfolio.asset'].with_user(self.alice).create(
            {'name': 'Private shares', 'portfolio_id': self.family.id, 'asset_type': 'stock'})
        with patch(FH_FETCH, return_value=({'AAPL': 1.0, 'MSFT': 1.0}, {})) as fetch:
            self.env['portfolio.asset']._cron_sync_prices()
        self.assertEqual(fetch.call_args.kwargs['symbols'], {'AAPL', 'MSFT'})
        self.assertFalse(plain.sync_error)

    def test_viewer_cannot_trigger_stock_sync(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.aapl.with_user(self.carol).action_sync_prices()

    # ---- the HTTP client itself ---------------------------------------------
    def _client(self, responses, **kwargs):
        class Resp:
            def __init__(self, code=200, payload=None, bad_json=False):
                self.status_code, self._p, self._bad = code, payload, bad_json
            def json(self):
                if self._bad:
                    raise ValueError
                return self._p
        queue = [r if not isinstance(r, tuple) else Resp(*r) for r in responses]
        with patch(FH_GET, side_effect=queue) as get:
            result = finnhub.fetch_quotes(kwargs.pop('symbols'), 'secret-key', **kwargs)
        return result, get

    def test_client_parses_quotes_and_keeps_key_out_of_the_url(self):
        (prices, errors), get = self._client([(200, {'c': 215.5, 't': 1}), (200, {'c': 410})], symbols=['MSFT', 'AAPL'])
        self.assertEqual(prices, {'AAPL': 215.5, 'MSFT': 410.0})
        self.assertEqual(errors, {})
        for call in get.call_args_list:
            self.assertEqual(call.kwargs['headers']['X-Finnhub-Token'], 'secret-key')
            self.assertNotIn('secret-key', str(call.args) + str(call.kwargs['params']))

    def test_client_unknown_symbol_returns_zeros_from_finnhub(self):
        (prices, errors), _get = self._client(
            [(200, {'c': 0, 'd': None, 't': 0}), (200, ['x']), (200, None, True), (200, {'c': True})],
            symbols=['A', 'B', 'C', 'D'])
        self.assertEqual(prices, {})
        self.assertEqual(set(errors), {'A', 'B', 'C', 'D'})

    def test_client_status_codes(self):
        (prices, errors), _get = self._client([(403,), (500,), (200, {'c': 5})], symbols=['A', 'B', 'C'])
        self.assertEqual(prices, {'C': 5.0})
        self.assertIn('free', errors['A'])
        self.assertIn('500', errors['B'])
        with self.assertRaises(finnhub.FinnhubError):
            self._client([(401,)], symbols=['A'])
        with self.assertRaises(finnhub.FinnhubError):
            finnhub.fetch_quotes(['A'], '')

    def test_client_rate_limit_keeps_what_it_got_and_stops(self):
        (prices, errors), get = self._client([(200, {'c': 1}), (429,), (200, {'c': 9})], symbols=['A', 'B', 'C'])
        self.assertEqual(prices, {'A': 1.0})
        self.assertEqual(set(errors), {'B', 'C'})
        self.assertIn('429', errors['B'])
        self.assertEqual(get.call_count, 2)          # did not keep hammering after the 429

    def test_client_network_error_reports_remaining_symbols(self):
        import requests
        (prices, errors), _get = self._client([(200, {'c': 1}), requests.Timeout()], symbols=['A', 'B', 'C'])
        self.assertEqual(prices, {'A': 1.0})
        self.assertEqual(set(errors), {'B', 'C'})
