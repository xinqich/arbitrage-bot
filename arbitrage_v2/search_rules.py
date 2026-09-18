"""Versioned search preferences, separate from immutable route economics."""
import json

VERSION = 'cs2-sale-priority-v1'
DEFAULTS = {'minimum_purchase_cents': 10, 'narrow_spread_bps': 1000}


def validate(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULTS)
            or type(value['minimum_purchase_cents']) is not int
            or not 1 <= value['minimum_purchase_cents'] <= 1000000
            or type(value['narrow_spread_bps']) is not int
            or not 0 <= value['narrow_spread_bps'] <= 10000):
        raise ValueError('Invalid minimum purchase price or spread threshold.')
    return dict(value)


def current(journal):
    with journal.connect() as db:
        row = db.execute("SELECT payload FROM records WHERE category='search_settings' ORDER BY seq DESC LIMIT 1").fetchone()
    return validate(json.loads(row[0])['settings']) if row else dict(DEFAULTS)


def leg(source, venue, mode, quote, settings):
    def rows(side):
        value = source.get('books', {}).get(venue+'_'+side, {}).get('value')
        return value.get('levels', [value]) if value else []
    bids, asks = rows('bid'), rows('ask')
    ask = min((r['price_cents'] for r in asks), default=None)
    bid = max((r['price_cents'] for r in bids), default=None)
    worst = min((r['price_cents'] for r in quote.get('fills', [])), default=None)
    consistent = ask is not None and bid is not None and bid <= ask
    narrow = (mode == 'current_bids' and quote['complete'] and consistent and worst is not None
              and (ask-worst)*10000 < ask*settings['narrow_spread_bps'])
    priority = (1 if narrow else 2) if mode == 'current_bids' else 3 if mode == 'midpoint' else 4
    return {'priority': priority, 'quantity': quote['quantity'],
        'covered_quantity': quote['quantity'] if mode == 'current_bids' and quote['complete'] else None,
        'available_bid_quantity': sum(r['quantity'] for r in bids) if bids else None,
        'best_spread_pct': round((ask-bid)*100/ask, 4) if consistent else None,
        'batch_spread_pct': round((ask-worst)*100/ask, 4) if consistent and worst is not None else None,
        'comparison_ask_cents': ask, 'worst_used_bid_cents': worst,
        'reason': ('fully_covered_narrow_spread' if narrow else
                   'fully_covered_other_bids' if mode == 'current_bids' else 'assumed_selling_price')}


def annotate(pred, a, b, settings):
    a = dict(a, books={k:v for k,v in a['books'].items() if v.get('input_kind') == pred['input_kind']})
    b = dict(b, books={k:v for k,v in b['books'].items() if v.get('input_kind') == pred['input_kind']})
    sales = pred['sale_scenarios']
    legs = {'steam': leg(a, 'steam', sales['steam'], pred['quote_legs']['steam_sale'], settings),
            'dmarket': leg(b, 'dmarket', sales['dmarket'], pred['quote_legs']['exit'], settings)}
    pred.update(ranking_version=VERSION, search_settings=dict(settings), sale_quality=legs,
                route_priority=max(r['priority'] for r in legs.values()))


def sort_key(p):
    # Priority orders profitable opportunities; a loss is never promoted above a gain.
    return ((p['predicted_net_cents'] or 0) <= 0, p.get('route_priority', 4), p['predicted_net_cents'] is None,
            -(p['predicted_net_cents'] or 0), p['entry_cost_cents'], p['item_a'], p['item_b'],
            p['quantity_a'], p['quantity_b'], p['sale_scenarios']['steam'], p['sale_scenarios']['dmarket'])


def return_choices(candidate, purchase_rows, wallet, net, settings):
    """Only the maximum destination receipts within each bid priority survive."""
    from .depth import book, quote, total
    if not purchase_rows or purchase_rows[0]['price_cents'] < settings['minimum_purchase_cents']:
        return []
    bids=book(candidate,'dmarket_bid')
    source={'books':{'dmarket_bid':{'value':candidate['dmarket_bid']}}}
    comparison=candidate.get('comparison_ask')
    if comparison:
        source['books']['dmarket_ask']={'value':comparison['value']}
    limits=[total(bids)]
    if comparison:
        ask=comparison['value']['price_cents']
        if max(r['price_cents'] for r in bids)<=ask:
            narrow=sum(r['quantity'] for r in bids if (ask-r['price_cents'])*10000 < ask*settings['narrow_spread_bps'])
            if 0<narrow<limits[0]:limits.append(narrow)
    result=[];seen=set()
    for limit in limits:
        buy=quote(purchase_rows,limit,budget=wallet)
        if not buy['quantity'] or buy['quantity'] in seen:continue
        seen.add(buy['quantity']);sale=quote(bids,buy['quantity'],net)
        if sale['net_cents']<=0:continue
        quality=leg(source,'dmarket','current_bids',sale,settings)
        result.append((quality['priority'],buy,sale,quality))
    return result
