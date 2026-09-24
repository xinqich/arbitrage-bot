"""Read-only historical screening experiment; no provider requests or bot writes."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import csv
import gzip
import json
from pathlib import Path
import random
import re
import sqlite3
import statistics
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent)
    parser.add_argument('--archive',type=Path,help='Replay saved public evidence instead of reading the journal')
    args=parser.parse_args();root=args.repo.resolve();out=args.output.resolve()
    cfg=json.loads((Path(__file__).parent/'protocol.json').read_text())
    sys.path.insert(0,str(root))
    from arbitrage_v2.evidence import utc,stamp
    from arbitrage_v2.discovery import snapshot, _inputs, SALE_BOOKS
    from arbitrage_v2.prediction import calculate,steam_net,dmarket_net
    from arbitrage_v2.depth import book,total
    start,end=utc(cfg['freeze_at']),utc(cfg['outcome_end'])
    policy=json.loads((root/'config/research_assumptions.json').read_text())
    sf=policy['steam_fee'];df=policy['dmarket_fee'];cap=cfg['capital_cents']
    def sn(p):return steam_net(p,sf['steam_bps'],sf['game_bps'],sf['minimum_steam_cents'],sf['minimum_game_cents'])
    def dn(p):return dmarket_net(p,df['bps'],df['minimum_cents'])
    def sg(p):return p+max(sf['minimum_steam_cents'],p*sf['steam_bps']//10000)+max(sf['minimum_game_cents'],p*sf['game_bps']//10000)
    bulk={};captures=defaultdict(lambda:defaultdict(list));attempts=defaultdict(list)
    steam_snapshot=None;steam_id=None
    if args.archive:
        archive=json.loads(gzip.decompress(args.archive.read_bytes()) if args.archive.suffix=='.gz' else args.archive.read_text(encoding='utf-8'))
        steam_snapshot=archive['steam_snapshot'];steam_id=archive['steam_id'];bulk=archive['catalogue']
        for c in archive['captures']:captures[c['title']][c['kind']].append(c)
        for r in archive['attempts']:attempts[tuple(r['key'])]=[utc(t) for t in r['times']]
    else:
      with sqlite3.connect((root/'data/research.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        for rid,encoded in db.execute("SELECT id,payload FROM records WHERE category='screening_snapshot' ORDER BY seq"):
            p=json.loads(encoded)
            if utc(p['retrieved_at'])<=start and p.get('published_at') and 0<=(start-utc(p['published_at'])).total_seconds()<=cfg['bulk_max_age_seconds']:
                steam_snapshot=p;steam_id=rid
        if not steam_snapshot:raise RuntimeError('No pre-cutoff usable-age Steam file')
        for rid,recorded,encoded in db.execute("SELECT id,recorded_at,payload FROM records WHERE category='capture' ORDER BY seq"):
            c=json.loads(encoded)
            at=utc(c['retrieved_at'])
            if c.get('input_kind')!='recorded' or utc(recorded)>end:continue
            if c['kind']=='catalogue' and c['provider']=='dmarket' and at<=start and c['status']==200 and not c.get('error'):
                for item in c['payload']['items']:
                    if 0<=(start-utc(item['observed_at'])).total_seconds()<=cfg['bulk_max_age_seconds']:
                        bulk[item['title']]=dict(item,evidence_id=rid)
            if start<=at<=end and c['kind'] in ('details','offers','targets'):
                captures[c['title']][c['kind']].append(dict(c,record_id=rid,_recorded_at=recorded))
        # Keep timestamps only, not full request payloads or credentials.
        for (encoded,) in db.execute("SELECT payload FROM records WHERE category='request_attempt'"):
            r=json.loads(encoded)
            if not r.get('started_at'):continue
            at=utc(r['started_at'])
            if start<=at<=end and r.get('kind') in ('details','offers','targets'):
                attempts[(r.get('title'),r['kind'],r['provider'])].append(at)
    for timestamps in attempts.values():timestamps.sort()
    summaries=steam_snapshot['items']
    outcomes={};source_rows=[]
    for title,kinds in captures.items():
        if 'details' not in kinds:continue
        # First Steam check is fixed without looking at whether it succeeded.
        detail=min(kinds['details'],key=lambda c:utc(c['retrieved_at']))
        checked=utc(detail['retrieved_at']);chosen={'details':detail}
        for kind in ('offers','targets'):
            close=[c for c in kinds.get(kind,[]) if abs((utc(c['retrieved_at'])-checked).total_seconds())<=cfg['pair_max_skew_seconds']]
            if close:chosen[kind]=min(close,key=lambda c:abs((utc(c['retrieved_at'])-checked).total_seconds()))
        as_of=stamp(end)
        row=snapshot(None,title,as_of,cfg['detail_max_age_seconds'],index={title:chosen})
        cost=0
        for kind,c in chosen.items():
            timestamps=attempts[(title,kind,c['provider'])]
            lo=utc(c.get('started_at',c['retrieved_at']));hi=utc(c['retrieved_at'])
            # A successfully archived response proves at least one request.
            cost+=max(1,bisect_right(timestamps,hi)-bisect_left(timestamps,lo))
        # Missing venue checks consume a reserved request, so missing data is
        # not rewarded with artificially cheap admission to the experiment.
        cost+=3-len(chosen)
        outcomes[title]=dict(snapshot=row,cost=cost,capture_ids=[c['record_id'] for c in chosen.values()],
            collected_at=as_of,complete=len(chosen)==3,parser=(detail.get('payload') or {}).get('provenance',{}).get('parser_version'))
        source_rows.extend(chosen.values())
    partners=[t for t in cfg['fixed_partner_names'] if t in outcomes and outcomes[t]['snapshot']['books']]
    # Select partners by the predeclared names, not their profitability.
    if not partners:raise RuntimeError('No reference partner evidence')
    cache={};pair_cache={}
    def pair(a_title,b_title):
        key=(a_title,b_title)
        if key in pair_cache:return pair_cache[key]
        a=outcomes[a_title]['snapshot'];b=outcomes[b_title]['snapshot'];best_bid=None;best_any=None;bid=None;any_route=None
        for sm,sources_a in SALE_BOOKS['steam'].items():
            if not {'dmarket_ask',*sources_a}<=a['books'].keys():continue
            aa=_inputs(a,('dmarket_ask',*sources_a))
            cost=aa['dmarket_ask']['price_cents']
            if cost<cfg['minimum_purchase_cents']:continue
            upper=min(cap//cost,total(book(aa,'dmarket_ask')),1000)
            if sm=='current_bids':upper=min(upper,total(book(aa,'steam_bid')))
            for dm,sources_b in SALE_BOOKS['dmarket'].items():
                if not {'steam_ask',*sources_b}<=b['books'].keys():continue
                bb=_inputs(b,('steam_ask',*sources_b))
                if bb['steam_ask']['price_cents']<cfg['minimum_purchase_cents']:continue
                for quantity in range(1,upper+1):
                    try:p=calculate(aa,bb,quantity,policy,sm,dm,cache=cache)
                    except ValueError:continue
                    if p['entry_cost_cents']>cap or p['predicted_net_cents'] is None:continue
                    net=p['predicted_net_cents']
                    concise={k:p[k] for k in ('item_a','item_b','quantity_a','quantity_b','entry_cost_cents','predicted_net_cents','steam_wallet_residual_cents')}
                    concise['steam_sale']=sm;concise['dmarket_sale']=dm
                    if best_any is None or net>best_any:best_any=net;any_route=concise
                    if sm==dm=='current_bids' and (best_bid is None or net>best_bid):best_bid=net;bid=concise
        pair_cache[key]=dict(bid=best_bid,any=best_any,bid_route=bid,any_route=any_route)
        return pair_cache[key]
    family_pattern=r' \((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$'
    rows=[];excluded=Counter()
    for title,outcome in sorted(outcomes.items()):
        if title in partners:continue
        d=bulk.get(title,{});s=summaries.get(title,{})
        ask=d.get('dmarket_ask_cents');summary=s.get('price_cents')
        if not ((ask and 10<=ask<=cap) or (summary and 10<=summary<=cap)):
            excluded['outside_predeclared_screening_price_range']+=1;continue
        family=re.sub(family_pattern,'',title).removeprefix('StatTrak™ ').removeprefix('Souvenir ')
        split='holdout' if int(sha256(family.encode()).hexdigest(),16)%5<2 else 'training'
        books=outcome['snapshot']['books']
        row=dict(title=title,family=family,split=split,cost=outcome['cost'],period=s.get('period'),
            summary_cents=summary,dm_ask_hint=ask,dm_sale_hint=d.get('dmarket_bid_cents') or d.get('dmarket_ask_cents'),
            dm_sale_hint_method='bid' if d.get('dmarket_bid_cents') else 'listing',
            hints_time=d.get('observed_at'),steam_snapshot_id=steam_id,dm_hint_id=d.get('evidence_id'),
            capture_ids=outcome['capture_ids'],collected_at=outcome['collected_at'],parser=outcome['parser'],
            complete=outcome['complete'],books=sorted(books),issues=outcome['snapshot']['issues'],
            dm_ask=books.get('dmarket_ask',{}).get('value',{}).get('price_cents'),
            dm_bid=books.get('dmarket_bid',{}).get('value',{}).get('price_cents'),
            steam_ask=books.get('steam_ask',{}).get('value',{}).get('price_cents'),
            steam_bid=books.get('steam_bid',{}).get('value',{}).get('price_cents'))
        for side in ('outward','returning'):
            found=[pair(title,p) if side=='outward' else pair(p,title) for p in partners]
            for method in ('bid','any'):
                priced=[r[method] for r in found if r[method] is not None]
                row[side+'_'+method]=max(priced) if priced else None
        rows.append(row)
    print('Replay outcomes prepared:',len(rows),'items;',len(pair_cache),'pair calculations',flush=True)
    def scores(row,hypothesis):
        p=row['summary_cents'];ask=row['dm_ask_hint'];sale=row['dm_sale_hint']
        outward=((sn(p) if hypothesis=='buyer_gross' else p)/ask-1)*100 if p and ask and 10<=ask<=cap else None
        returning=(dn(sale)/(p if hypothesis=='buyer_gross' else sg(p))-1)*100 if p and sale else None
        return outward,returning
    def trial(group,threshold,seed,hypothesis,control=False):
        rng=random.Random(seed);random_order=list(group);rng.shuffle(random_order)
        tie={r['title']:i for i,r in enumerate(random_order)}
        queues={};positions=defaultdict(int)
        for i,leg in enumerate(('outward','returning')):
            queues[leg]=sorted((r for r in group if scores(r,hypothesis)[i] is not None and scores(r,hypothesis)[i]>=threshold),
                key=lambda r:(-scores(r,hypothesis)[i],tie[r['title']]))
        queues['exploration']=random_order
        selected=[];seen=set();used=0;turn=0;budget=cfg['candidate_http_budget']
        def take(leg):
            while positions[leg]<len(queues[leg]):
                r=queues[leg][positions[leg]];positions[leg]+=1
                if r['title'] not in seen:return r
            return None
        while used<budget:
            leg='exploration' if control else ('outward','outward','returning','returning','exploration')[turn%5]
            r=take(leg) or take('exploration')
            if r is None:break
            seen.add(r['title']);turn+=1
            if used+r['cost']>budget:
                used=budget;break # Same cap: final incomplete item contributes no route.
            used+=r['cost'];selected.append(r)
        def useful(row,method):return any(row[s+'_'+method] is not None and row[s+'_'+method]>0 for s in ('outward','returning'))
        return dict(requests=used,items=len(selected),bid_useful=sum(useful(r,'bid') for r in selected),
            any_useful=sum(useful(r,'any') for r in selected),
            best_bid=max((r[s+'_bid'] for r in selected for s in ('outward','returning') if r[s+'_bid'] is not None),default=None),
            best_any=max((r[s+'_any'] for r in selected for s in ('outward','returning') if r[s+'_any'] is not None),default=None),
            names=[r['title'] for r in selected])
    def summary(trials):
        return {k:round(statistics.mean(t[k] or 0 for t in trials),4) for k in ('requests','items','bid_useful','any_useful','best_bid','best_any')}
    experiments={}
    for hypothesis in ('buyer_gross','seller_net'):
        train=[r for r in rows if r['split']=='training'];hold=[r for r in rows if r['split']=='holdout']
        grid=[]
        for threshold in cfg['threshold_percent']:
            results=[trial(train,threshold,seed,hypothesis) for seed in range(cfg['training_repeats'])]
            grid.append(dict(threshold=threshold,**summary(results)))
        winner=max(grid,key=lambda r:(r['bid_useful'],-r['threshold']))['threshold']
        trained=[trial(hold,winner,10000+seed,hypothesis) for seed in range(cfg['holdout_repeats'])]
        control=[trial(hold,0,10000+seed,hypothesis,True) for seed in range(cfg['holdout_repeats'])]
        differences=sorted(a['bid_useful']-b['bid_useful'] for a,b in zip(trained,control))
        experiments[hypothesis]=dict(training_grid=grid,chosen_threshold=winner,
            holdout_hint=summary(trained),holdout_control=summary(control),
            holdout_without_cutoff=summary([trial(hold,-float('inf'),10000+seed,hypothesis) for seed in range(cfg['holdout_repeats'])]),
            paired_delta=round(statistics.mean(differences),4),
            shuffle_delta_range_95=[differences[int(.025*len(differences))],differences[int(.975*len(differences))-1]],
            hint_win_fraction=sum(d>0 for d in differences)/len(differences),
            example_hint=trained[0],example_control=control[0])
    report=dict(protocol=cfg,cohort_items=len(rows),cohort_families=len({r['family'] for r in rows}),
        split_counts=dict(Counter(r['split'] for r in rows)),
        split_families={s:len({r['family'] for r in rows if r['split']==s}) for s in ('training','holdout')},
        available_details=len(outcomes),universe_with_dm_hints=len(bulk),partners=partners,
        partner_http_cost=sum(outcomes[t]['cost'] for t in partners),
        steam_snapshot=dict(record_id=steam_id,published_at=steam_snapshot['published_at'],retrieved_at=steam_snapshot['retrieved_at'],body_sha256=steam_snapshot['body_sha256']),
        excluded=dict(excluded),complete_items=sum(r['complete'] for r in rows),
        priced_bid_items=sum(any(r[s+'_bid'] is not None for s in ('outward','returning')) for r in rows),
        useful_bid_items=sum(any(r[s+'_bid'] is not None and r[s+'_bid']>0 for s in ('outward','returning')) for r in rows),
        useful_any_items=sum(any(r[s+'_any'] is not None and r[s+'_any']>0 for s in ('outward','returning')) for r in rows),
        experiments=experiments,policy=policy,rows=rows,
        best_bid_routes=sorted([r['bid_route'] for r in pair_cache.values() if r['bid_route'] and r['bid']>0],key=lambda r:-r['predicted_net_cents'])[:10])
    (out/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    encoded=json.dumps(dict(steam_snapshot=steam_snapshot,steam_id=steam_id,catalogue=bulk,captures=source_rows,
        attempts=[dict(key=k,times=[stamp(t) for t in v]) for k,v in sorted(attempts.items())]),ensure_ascii=False).encode('utf-8')
    (out/'evidence.json.gz').write_bytes(gzip.compress(encoded,mtime=0))
    columns=['title','family','split','cost','period','summary_cents','dm_ask_hint','dm_sale_hint','steam_ask','steam_bid','outward_bid','returning_bid','outward_any','returning_any']
    with (out/'items.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows({k:r[k] for k in columns} for r in rows)
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','policy','protocol','best_bid_routes','experiments')},indent=2),flush=True)
    for name,exp in experiments.items():print(name,json.dumps({k:v for k,v in exp.items() if not k.startswith('example') and k!='training_grid'}),flush=True)


if __name__=='__main__':main()
