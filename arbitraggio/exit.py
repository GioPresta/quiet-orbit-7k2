"""Liquidation at executable bids, with sell fees. No forecast of future exits."""
from .engine import D,number


def sell_fee(fee,venue,price,quantity):
    if venue=='limitless':
        return quantity*price*D('.015'),True  # documented conservative upper CLOB sell rate
    if fee.model=='shares':
        return quantity*price*fee.rate,True
    if fee.model=='opinion':
        return max(quantity*price*fee.rate*price*(1-price),fee.minimum),True
    if fee.model=='predict':return quantity*fee.rate*min(price,1-price),fee.estimated
    if fee.model in ('cash_curve','shares_curve'):return quantity*fee.rate*price*(1-price),fee.estimated
    return quantity*price*fee.rate,fee.estimated


def liquidate(allocation,book,fee):
    requested=number(allocation['net_shares']);left=requested
    proceeds=D(0);fees=D(0);fills=[];estimated=False
    for price,size in sorted(book.get('bids',[]),reverse=True):
        q=min(left,size)
        if q<=0:break
        charge,approx=sell_fee(fee,allocation['venue'],price,q)
        proceeds+=price*q;fees+=charge;estimated=estimated or approx
        fills.append(dict(price=price,shares=q,fee=charge));left-=q
    return dict(venue=allocation['venue'],market=allocation['market'],outcome=allocation['outcome'],
                requested_shares=requested,sellable_shares=requested-left,unfilled_shares=left,
                fully_sellable=left==0,gross_proceeds=proceeds,sell_fees=fees,
                net_proceeds=proceeds-fees,fee_estimated=estimated,fills=fills,
                best_bid=book.get('bids',[None])[0][0] if book.get('bids') else None)


def evaluate_exit(result,books,fees,fixed_exit_cost=0):
    legs=[liquidate(a,b,f) for a,b,f in zip(result['allocations'],books,fees)]
    complete=all(l['fully_sellable'] for l in legs)
    proceeds=sum((l['net_proceeds'] for l in legs),D(0))-number(fixed_exit_cost)
    profit=proceeds-result['total_cost'] if complete else None
    return dict(fully_sellable=complete,net_proceeds=proceeds,profit=profit,
                profitable_now=bool(complete and profit>0),legs=legs,
                sell_fees=sum((l['sell_fees'] for l in legs),D(0)),fixed_exit_cost=number(fixed_exit_cost),
                fee_estimated=any(l['fee_estimated'] for l in legs),
                note='Rivendita istantanea teorica ai bid letti. Nessuna previsione sui tempi di uscita futuri.')


def convergence_exit(result,fees,fixed_exit_cost=0):
    """Conditional future bids sum to $1. No inferred future depth or timing."""
    allocations=result['allocations']
    if len(allocations)!=2:return None
    q=number(allocations[0]['net_shares'])
    if q<=0 or any(number(a['net_shares'])!=q for a in allocations):return None
    entry=[number(a['notional'])/number(a['gross_shares']) for a in allocations]
    gap=1-sum(entry)
    if gap<=0:return None
    scenarios=[]
    for weight,label in [(D(0),'Converge il secondo lato'),(D('.5'),'Convergenza simmetrica'),(D(1),'Converge il primo lato')]:
        p=max(D('0.000001'),min(D('.999999'),entry[0]+gap*weight))
        prices=[p,1-p]
        charges=[sell_fee(f,a['venue'],price,q) for f,a,price in zip(fees,allocations,prices)]
        sell=sum((c[0] for c in charges),D(0));net=q-sell-number(fixed_exit_cost)
        scenarios.append(dict(label=label,prices=prices,gross_proceeds=q,sell_fees=sell,
                              net_proceeds=net,profit=net-number(result['total_cost']),
                              roi_pct=(net-number(result['total_cost']))/number(result['total_cost'])*100,
                              fee_estimated=any(c[1] for c in charges)))
    middle=scenarios[1]
    return dict(**middle,scenarios=scenarios,scenario_min_profit=min(s['profit'] for s in scenarios),
                scenario_max_profit=max(s['profit'] for s in scenarios),
                condition='Bid futuri YES + NO = $1 e profondità sufficiente per vendere tutte le quote nette',
                hypothetical=True,depth_confirmed=False,time_estimate=None,
                fixed_exit_cost=number(fixed_exit_cost),
                partial_profit_target=number(result['profit'])*D('.5'),
                partial_target_net_proceeds=number(result['total_cost'])+number(result['profit'])*D('.5'),
                note='Simulazione condizionata, non profitto eseguibile oggi. La scomparsa dell’arbitraggio agli ask non implica questi bid.')
