"""Two-leg research selection; summary hints never reach route arithmetic."""
from collections import deque
from fractions import Fraction

from . import csgotrader
from .collection_settings import settings as collection_settings
from .discovery import snapshot
from .prediction import steam_net, dmarket_net, _steam_observation

VERSION = 'balanced-screening-v1'


def select(journal, catalogue, seeds, capital, rules, slots, index, at,
           refresh_seconds=3600, exclude=(), config=None, policy=None, should_stop=None):
    cfg = collection_settings(config)
    steam_state = csgotrader.view(journal)
    steam_hints = csgotrader.hints(journal, at, cfg['screening_max_age_seconds'])
    names = set(catalogue['items']) | steam_state['names'] | {r['title'] for r in seeds} | set(index)
    names.difference_update(exclude)
    checked = catalogue['progress'].get('checked', {})
    # Callers normally supply the route policy. Defaults preserve the old public
    # helper's signature for offline callers, never alter a route's fee policy.
    policy = policy or {'steam_fee': {'steam_bps':500,'game_bps':1000,
        'minimum_steam_cents':1,'minimum_game_cents':1}, 'dmarket_fee': {'bps':1000,'minimum_cents':1}}
    sf, df = policy['steam_fee'], policy['dmarket_fee']
    def net_steam(price):
        return steam_net(price, sf['steam_bps'], sf['game_bps'], sf['minimum_steam_cents'], sf['minimum_game_cents'])
    def net_dm(price):
        return dmarket_net(price, df['bps'], df['minimum_cents'])
    candidates = {}
    for title in sorted(names):
        if should_stop and should_stop():
            break
        captures=index.get(title,{})
        recent=(set(captures)=={'details','offers','targets'} and all(
            c.get('input_kind')=='recorded' and not c.get('error') and c.get('status')==200
            and csgotrader.fresh(at,c.get('retrieved_at'),refresh_seconds-0.001) for c in captures.values()))
        if recent:
            try:
                _steam_observation(captures['details'],title,at,max(0,refresh_seconds-0.001))
            except (ValueError,KeyError,TypeError):
                recent=False
        # A successful empty order book is still a recent check. Do not keep
        # polling an item merely because no eligible supply was observed.
        if recent:continue
        books = snapshot(journal, title, at, cfg['freshness_seconds'], index=index)['books'] if title in index else {}
        books = {k:v for k,v in books.items() if v['input_kind']=='recorded'}
        dm = catalogue['items'].get(title, {})
        if not csgotrader.fresh(at, dm.get('observed_at'), cfg['screening_max_age_seconds']):
            dm = {}
        st = steam_hints['items'].get(title, {})
        def detailed(name):
            row=books.get(name)
            if row is None:return None
            return dict(price_cents=row['value']['price_cents'], source='detailed',
                evidence_id=row['evidence_id'], observed_at=row['source_time'], method=name)
        def dm_hint(name):
            value=dm.get('dmarket_'+name+'_cents')
            return dict(price_cents=value, source='dmarket_summary', method='dmarket_'+name,
                observed_at=dm['observed_at'], evidence_id=dm.get('evidence_id')) if value else None
        steam_hint = dict(st, source='csgotrader_summary', method='historical_summary',
            observed_at=steam_state['snapshot'].get('published_at'),
            evidence_id=steam_state['snapshot'].get('record_id')) if st.get('price_cents') else None
        buy_a = detailed('dmarket_ask') or dm_hint('ask')
        sell_a = detailed('steam_bid') or detailed('steam_ask') or steam_hint
        buy_b = detailed('steam_ask') or steam_hint
        sell_b = detailed('dmarket_bid') or detailed('dmarket_ask') or dm_hint('bid') or dm_hint('ask')
        outward = returning = None
        if buy_a and sell_a and rules['minimum_purchase_cents'] <= buy_a['price_cents'] <= capital:
            outward=Fraction(net_steam(sell_a['price_cents']), buy_a['price_cents'])
        if buy_b and sell_b and buy_b['price_cents'] >= rules['minimum_purchase_cents']:
            returning=Fraction(net_dm(sell_b['price_cents']), buy_b['price_cents'])
        candidates[title] = dict(outward=outward, returning=returning, checked=checked.get(title,''),
            sources=dict(outward_purchase=buy_a, outward_sale=sell_a, return_purchase=buy_b, return_sale=sell_b))
    queues={leg:deque(sorted((t for t,r in candidates.items() if r[leg] is not None),
        key=lambda t:(-candidates[t][leg],candidates[t]['checked'],t))) for leg in ('outward','returning')}
    queues['exploration']=deque(sorted(candidates,key=lambda t:(candidates[t]['checked'],t)))
    count=catalogue['progress'].get('selection_count',0)
    chosen, audit, seen=[],[],set()
    pattern=cfg['screening_selection_pattern']
    def take(leg):
        q=queues[leg]
        while q:
            title=q.popleft()
            if title not in seen:return title
        return None
    for _ in range(min(slots,len(candidates))):
        leg=pattern[count % len(pattern)]
        title=take(leg)
        requested=leg
        if title is None:
            # Preserve unscored items in exploration rather than inventing a zero.
            for leg in ('outward','returning','exploration'):
                title=take(leg)
                if title is not None:break
        if title is None:break
        count+=1;seen.add(title)
        chosen.append(dict(app_id=730,title=title))
        row=candidates[title]
        audit.append(dict(title=title, requested_group=requested, selected_group=leg,
            outward_ratio=float(row['outward']) if row['outward'] is not None else None,
            return_ratio=float(row['returning']) if row['returning'] is not None else None,
            sources=row['sources']))
    return chosen,count,dict(version=VERSION,at=at,settings={k:cfg[k] for k in (
        'screening_max_age_seconds','screening_selection_pattern','freshness_seconds')},
        steam=steam_hints['status'], eligible_items=len(candidates), selections=audit)
