{
    'name': 'Wealth Portfolio Tracker',
    'version': '18.0.2.0.0',
    'category': 'Finance',
    'summary': 'Multi-asset portfolios with per-portfolio roles, an append-only ledger and live valuations',
    'description': """
Wealth Portfolio Tracker
========================
Track stocks, crypto, real estate and cash in shared portfolios.

* Per-portfolio roles (Owner / Editor / Viewer) enforced by record rules
* Append-only transaction ledger; positions and cost basis are derived from it
* Scheduled CoinGecko price sync (one batched request)
* Live dashboard (OWL) refreshed through the Odoo bus
""",
    'author': 'Mofeed Dozkanji',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'bus', 'web'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'data/cron_data.xml',
        'views/portfolio_views.xml',
        'views/asset_views.xml',
        'views/transaction_views.xml',
        'views/menus.xml',
    ],
    'demo': [
        'data/demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'wealth_portfolio/static/src/dashboard/dashboard.scss',
            'wealth_portfolio/static/src/dashboard/dashboard.js',
            'wealth_portfolio/static/src/dashboard/dashboard.xml',
        ],
    },
    'application': True,
    'installable': True,
}
