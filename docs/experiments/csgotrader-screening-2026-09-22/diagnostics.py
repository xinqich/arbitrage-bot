"""Descriptive follow-up; these results do not choose a new threshold."""
import json
from pathlib import Path
import statistics

root=Path(__file__).parent
p=json.loads((root/'results.json').read_text(encoding='utf-8'))
rows=p['rows'];sf=p['policy']['steam_fee'];df=p['policy']['dmarket_fee']

def gross(net):
    return net+max(sf['minimum_steam_cents'],net*sf['steam_bps']//10000)+max(sf['minimum_game_cents'],net*sf['game_bps']//10000)

def net(price):
    # Independent integer inversion of Steam's buyer-paid amount.
    return max((n for n in range(price+1) if gross(n)<=price),default=0)

def dm(price):return max(0,price-max(df['minimum_cents'],(price*df['bps']+9999)//10000))

def rank(values):
    return [1+sum(y<x for y in values)+(sum(y==x for y in values)-1)/2 for x in values]

def corr(pairs):
    if len(pairs)<3:return None
    return round(statistics.correlation(rank([a for a,b in pairs]),rank([b for a,b in pairs])),4)

out={}
for split in ('training','holdout'):
    rr=[r for r in rows if r['split']==split]
    z={}
    for side in ('outward','returning'):
        pairs=[];positive=[];buckets=[]
        for r in rr:
            summary=r['summary_cents'];price=r['dm_ask_hint'] if side=='outward' else r['dm_sale_hint']
            if not price or not summary:continue
            score=100*((net(summary)/price if side=='outward' else dm(price)/summary)-1)
            truth=r[side+'_bid'];pairs.append((score,truth,r))
            if truth is not None and truth>0:positive.append(score)
        for lo,hi in ((-float('inf'),0),(0,10),(10,20),(20,30),(30,50),(50,float('inf'))):
            group=[(score,truth,r) for score,truth,r in pairs if lo<=score<hi]
            buckets.append(dict(low=None if lo==-float('inf') else lo,high=None if hi==float('inf') else hi,
                items=len(group),priceable=sum(t is not None for _,t,_ in group),
                useful=sum(t is not None and t>0 for _,t,_ in group)))
        z[side]=dict(count=len(pairs),spearman_score_vs_route_net=corr([(s,t) for s,t,_ in pairs if t is not None]),
            profitable_below_zero=sum(v<0 for v in positive),profitable_total=len(positive),buckets=buckets)
    ratios=[r['summary_cents']/r['steam_bid'] for r in rr if r['steam_bid'] and r['summary_cents']]
    z['summary_over_current_bid']=dict(count=len(ratios),median=round(statistics.median(ratios),4),
        within_10_percent=sum(.9<=v<=1.1 for v in ratios),more_than_25_percent_higher=sum(v>1.25 for v in ratios))
    out[split]=z

families={s:{r['family'] for r in rows if r['split']==s} for s in ('training','holdout')}
assert not families['training'] & families['holdout']
assert len({r['title'] for r in rows})==len(rows)
for exp in p['experiments'].values():
    for k in ('example_hint','example_control'):
        t=exp[k]
        assert len(t['names'])==len(set(t['names']))==t['items']
        assert t['requests']<=p['protocol']['candidate_http_budget']
    assert exp['chosen_threshold'] in p['protocol']['threshold_percent']

out['validation']='Unique candidates, disjoint finish-family split, bounded equal request budgets checked.'
(root/'diagnostics.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out,indent=2))
