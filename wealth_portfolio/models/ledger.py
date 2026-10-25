"""Pure ledger maths, kept free of ORM calls so it is easy to read and to test.

The ledger (portfolio.transaction) is the single source of truth. An asset's
quantity and cost basis are *derived* by replaying its transactions in order,
using the weighted-average cost method:

* inflow  (buy, transfer_in):   quantity += q ; cost += q * price
* outflow (sell, transfer_out): cost -= q * (cost / quantity) ; quantity -= q
"""

EPS = 1e-9

INFLOW = ('buy', 'transfer_in')
OUTFLOW = ('sell', 'transfer_out')

OPPOSITE = {
    'buy': 'sell',
    'sell': 'buy',
    'transfer_in': 'transfer_out',
    'transfer_out': 'transfer_in',
}


def replay(transactions):
    """Replay transactions in (date, id) order.

    :param transactions: recordset (or any iterable) of portfolio.transaction
    :return: (quantity, cost_basis, lowest_quantity_reached)
    """
    quantity = 0.0
    cost = 0.0
    lowest = 0.0
    ordered = sorted(transactions, key=lambda t: (t.date, t._origin.id or 0))
    for tx in ordered:
        if tx.tx_type in INFLOW:
            quantity += tx.quantity
            cost += tx.quantity * tx.price
        else:
            average = cost / quantity if quantity > EPS else 0.0
            cost -= tx.quantity * average
            quantity -= tx.quantity
        lowest = min(lowest, quantity)
    if abs(quantity) < EPS:
        # Position fully closed: drop floating-point residue.
        quantity = cost = 0.0
    return quantity, cost, lowest
