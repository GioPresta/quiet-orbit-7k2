"""Depth-aware hedging in net received shares; all arithmetic uses Decimal."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

D = Decimal
ZERO = D('0')
ONE = D('1')


def number(value):
    n = D(str(value))
    if not n.is_finite():
        raise ValueError('Numero non finito')
    return n


@dataclass(frozen=True)
class Fee:
    # cash_flat: fraction of notional; cash_curve: rate*p*(1-p) per share.
    # shares: fraction withheld from purchased shares (not extra cash).
    model: str
    rate: D
    source: str
    conservative: bool = False
    minimum: D = ZERO
    estimated: bool = False

    def __post_init__(self):
        if self.model not in ('cash_flat', 'cash_curve', 'shares', 'shares_curve', 'predict', 'opinion'):
            raise ValueError('Modello fee sconosciuto')
        if not ZERO <= self.rate < ONE or not self.source or self.minimum < ZERO:
            raise ValueError('Fee non valida o fonte assente')

    def retained_fraction(self, price):
        if self.model == 'shares': return ONE-self.rate
        if self.model == 'shares_curve': return ONE-self.rate*(ONE-price)
        return ONE

    def unit(self, price):
        if self.model == 'shares_curve':
            gross = ONE/self.retained_fraction(price)
            return price*gross, gross
        if self.model == 'shares':
            return price / (ONE - self.rate), ONE / (ONE - self.rate)
        if self.model == 'predict':
            return price + self.rate * min(price, ONE-price), ONE
        if self.model == 'opinion':
            return price + self.rate * price * price * (ONE-price), ONE
        extra = self.rate * price
        if self.model == 'cash_curve':
            extra *= ONE - price
        return price + extra, ONE


@dataclass
class Leg:
    venue: str
    market: str
    outcome: str
    asks: list
    fee: Fee
    min_order: D = ZERO
    balance: D | None = None
    min_notional: D = ZERO

    def levels(self):
        result = []
        for p, size in sorted(self.asks):
            if not ZERO < p < ONE or size <= ZERO:
                raise ValueError('Livello order book non valido')
            cost, gross_per_net = self.fee.unit(p)
            net_size = size * self.fee.retained_fraction(p)
            result.append((net_size, cost, gross_per_net, p))
        return result


def optimize(legs, budget, fixed_cost=ZERO, step=D('0.000001'), require_full_budget=False):
    """Maximize absolute profit subject to budget, balances, depth and positive marginal edge.

    Complete-set payout is one unit per net share. Mapping/settlement compatibility
    is a separate prerequisite. Fees and costs must be denominated in same unit.
    """
    budget, fixed_cost, step = map(number, (budget, fixed_cost, step))
    if budget <= ZERO or fixed_cost < ZERO or step <= ZERO or len(legs) < 2:
        raise ValueError('Budget, costi, passo o numero posizioni non valido')
    if len({(l.venue, l.market, l.outcome) for l in legs}) != len(legs):
        raise ValueError('Posizioni duplicate')
    books = [l.levels() for l in legs]
    if any(not b for b in books):
        return None
    if fixed_cost >= budget:
        return None
    def allocation(leg, book, target):
        a = dict(venue=leg.venue, market=leg.market, outcome=leg.outcome,
                 gross_shares=ZERO, net_shares=target, notional=ZERO,
                 cash_fee=ZERO, withheld_shares=ZERO, withheld_fee_value=ZERO, total=ZERO,
                 limit_price=ZERO, fills=[], fee_source=leg.fee.source,
                 conservative_fee=leg.fee.conservative, estimated_fee=leg.fee.estimated)
        left = target
        for capacity, unit_cost, gross_per_net, p in book:
            take = min(left, capacity)
            if take <= ZERO:
                break
            gross = take * gross_per_net
            notional = gross * p
            fee = ZERO if leg.fee.model in ('shares','shares_curve') else max(ZERO, take * unit_cost - notional)
            # Opinion floor is per fill. A price level can hide multiple maker fills:
            # one fill per price level is an explicit estimate, never an exact fee.
            if leg.fee.model == 'opinion':
                fee = max(fee, leg.fee.minimum)
            a['gross_shares'] += gross
            a['notional'] += notional
            a['cash_fee'] += fee
            a['total'] += notional + fee
            a['withheld_fee_value'] += (gross-take)*p
            a['limit_price'] = max(a['limit_price'], p)
            a['fills'].append(dict(price=p, gross_shares=gross, net_shares=take,
                                   notional=notional, cash_fee=fee,withheld_shares=gross-take,withheld_fee_value=(gross-take)*p))
            left -= take
        a['withheld_shares'] = a['gross_shares'] - target
        return a

    def evaluate(q):
        allocations = [allocation(l, b, q) for l, b in zip(legs, books)]
        total = sum((a['total'] for a in allocations), ZERO) + fixed_cost
        balances = {}
        for l, a in zip(legs, allocations):
            balances[l.venue] = balances.get(l.venue, ZERO) + a['total']
        fits = total <= budget and all(l.balance is None or balances[l.venue] <= l.balance for l in legs)
        return allocations, total, fits

    depth = min(sum((row[0] for row in book), ZERO) for book in books)
    max_units = int((depth / step).to_integral_value(rounding=ROUND_DOWN))
    # Monotone cost/balance search on the exact quantity grid.
    lo, hi = 0, max_units
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if evaluate(step * mid)[2]:
            lo = mid
        else:
            hi = mid - 1
    limit = lo * step
    if limit <= ZERO:
        return None
    candidates = {limit}
    # Cost is piecewise linear, with jumps when another price-level fee activates.
    # Its profit maximum is at a depth boundary, floor kink, or budget boundary.
    for leg, book in zip(legs, books):
        cumulative = ZERO
        for capacity, unit_cost, gross_per_net, price in book:
            points = [cumulative, cumulative + capacity]
            if leg.fee.model == 'opinion':
                fee_per_net = unit_cost - price * gross_per_net
                if fee_per_net > ZERO:
                    kink = cumulative + leg.fee.minimum / fee_per_net
                    if kink <= cumulative + capacity:
                        points.append(kink)
            for point in points:
                rounded = (point/step).to_integral_value(rounding=ROUND_DOWN)*step
                for q in (rounded, rounded+step):
                    if ZERO < q <= limit:
                        candidates.add(q)
            cumulative += capacity
    best = None
    for target in ([limit] if require_full_budget else sorted(candidates)):
        allocations, total, fits = evaluate(target)
        if not fits or any(a['gross_shares'] < l.min_order or a['notional'] < l.min_notional
                           for l, a in zip(legs, allocations)):
            continue
        # Only quantity-grid dust is allowed; never silently downsize the budget.
        tolerance = step * sum((max(row[1] for row in book) for book in books), ZERO) + D('0.000000001')
        if require_full_budget and budget - total > tolerance:
            continue
        profit = target - total
        if profit <= ZERO:
            continue
        if best is None or profit > best['profit']:
            best = dict(payout=target, total_cost=total, fixed_cost=fixed_cost,
                        profit=profit, roi_pct=profit/total*100,
                        unused_budget=budget-total, allocations=allocations)
    return best
