"""Historical cost of currently held items; never a market valuation or open P&L."""
from collections import OrderedDict
from .routes import _state


def route_cost(journal, route_id, db=None):
    route, _, events, _, assets, _, _, _, resolved = _state(journal, route_id, db)
    if resolved:
        return {'route_id':route_id, 'mode':route['mode'], 'items':[], 'cost_cents':0, 'complete':True}
    groups = OrderedDict()
    for event in events:
        groups.setdefault(event['reference'], []).append(event)
    recovery_refs = {r['reference'] for r in journal.records('recovery_link', db) if r['route_id']==route_id}
    holdings = {}
    for reference, group in groups.items():
        acquired = [e for e in group if e['kind']=='asset' and e['quantity_delta']>0]
        payments = [e for e in group if e['kind']=='movement' and e['account'] in
                    {'dmarket_regular','dmarket_tradable','steam_wallet','csfloat_deposited','csfloat_spendable'}]
        cost = -sum(e['net_delta_cents'] for e in payments)
        attributable = len(acquired)==1 and cost>0 and all(e['net_delta_cents']<=0 for e in payments)
        for event in group:
            if event['kind']!='asset':
                continue
            key, change = event['asset_key'], event['quantity_delta']
            value = holdings.setdefault(key, {'quantity':0, 'cost_cents':0})
            if change>0:
                incoming = 0 if reference in recovery_refs else cost if attributable else None
                value['cost_cents'] = (value['cost_cents']+incoming
                                      if value['cost_cents'] is not None and incoming is not None else None)
            elif -change == value['quantity']:
                value['cost_cents'] = 0
            elif value['cost_cents'] is not None and value['quantity']>0:
                value['cost_cents'] -= value['cost_cents']*(-change)//value['quantity']
            else:
                value['cost_cents'] = None
            value['quantity'] += change
    items=[]
    for key, quantity in assets.items():
        if quantity:
            value=holdings.get(key,{})
            items.append({'asset_key':key, 'quantity':quantity,
                          'cost_cents':value.get('cost_cents') if value.get('quantity')==quantity else None})
    complete=all(i['cost_cents'] is not None for i in items)
    return {'route_id':route_id, 'mode':route['mode'], 'items':items, 'complete':complete,
            'cost_cents':sum(i['cost_cents'] for i in items) if complete else None,
            'basis':'Actual recorded purchase cost; return-item cost is Steam credit spent.'}


def summary(journal, db=None):
    routes=[route_cost(journal,r['route_id'],db) for r in journal.records('route',db)]
    result={'routes':routes}
    for mode in ('confirmed','paper'):
        selected=[r for r in routes if r['mode']==mode]
        complete=all(r['complete'] for r in selected)
        result[mode]={'complete':complete, 'cost_cents':sum(r['cost_cents'] for r in selected) if complete else None}
    return result
