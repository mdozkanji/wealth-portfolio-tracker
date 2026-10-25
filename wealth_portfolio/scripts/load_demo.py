"""Load the demo data into ANY existing database where wealth_portfolio is installed.

Odoo only loads a module's demo data when the database was created with demo data
enabled. If yours was not (the default of the web database manager), run:

    python odoo-bin shell -c odoo.conf -d <your_database> < /path/to/scripts/load_demo.py

Creates users alice / bob / carol (password = login), the portfolios "Family Wealth"
and "Alice Private" with assets and ledger entries. Safe to run twice.
"""
from odoo.tools import convert_file

if env.ref('wealth_portfolio.demo_user_alice', raise_if_not_found=False):
    print("Demo data already present: nothing to do.")
elif not env['ir.module.module'].search([('name', '=', 'wealth_portfolio'), ('state', '=', 'installed')]):
    print("wealth_portfolio is not installed in this database: install it first.")
else:
    convert_file(env, 'wealth_portfolio', 'data/demo.xml', {}, mode='init', noupdate=False, kind='demo')
    env.cr.commit()
    print("Demo data loaded. Log in as alice / alice, bob / bob or carol / carol.")
