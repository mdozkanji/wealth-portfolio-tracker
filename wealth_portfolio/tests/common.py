from odoo.tests.common import TransactionCase


class WealthCase(TransactionCase):
    """Three users and two portfolios.

    Family: alice = owner, bob = editor, carol = viewer
    Private: alice only (dave and everybody else must never see it)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        group_user = cls.env.ref('base.group_user')

        def make_user(login):
            return cls.env['res.users'].create({
                'name': login.title(), 'login': login, 'email': f'{login}@example.com',
                'groups_id': [(6, 0, [group_user.id])],
            })

        cls.alice, cls.bob, cls.carol, cls.dave = (make_user(n) for n in ('t_alice', 't_bob', 't_carol', 't_dave'))
        Portfolio = cls.env['portfolio.portfolio']
        cls.family = Portfolio.with_user(cls.alice).create({'name': 'Family'})
        cls.private = Portfolio.with_user(cls.alice).create({'name': 'Private'})
        # Creating a portfolio makes the creator its owner; alice then invites others.
        Member = cls.env['portfolio.member'].with_user(cls.alice)
        Member.create({'portfolio_id': cls.family.id, 'user_id': cls.bob.id, 'role': 'editor'})
        Member.create({'portfolio_id': cls.family.id, 'user_id': cls.carol.id, 'role': 'viewer'})

        Asset = cls.env['portfolio.asset'].with_user(cls.alice)
        cls.btc = Asset.create({'name': 'Bitcoin', 'portfolio_id': cls.family.id,
                                'asset_type': 'crypto', 'coingecko_id': 'bitcoin'})
        cls.flat = Asset.create({'name': 'Flat', 'portfolio_id': cls.family.id, 'asset_type': 'real_estate'})
        cls.secret = Asset.create({'name': 'Secret', 'portfolio_id': cls.private.id, 'asset_type': 'stock'})
        cls.tx_secret = cls.env['portfolio.transaction'].with_user(cls.alice).create({
            'asset_id': cls.secret.id, 'tx_type': 'buy', 'quantity': 10, 'price': 100})

    def buy(self, asset, qty, price, user=None, tx_type='buy', date=None):
        vals = {'asset_id': asset.id, 'tx_type': tx_type, 'quantity': qty, 'price': price}
        if date:
            vals['date'] = date
        return self.env['portfolio.transaction'].with_user(user or self.alice).create(vals)
