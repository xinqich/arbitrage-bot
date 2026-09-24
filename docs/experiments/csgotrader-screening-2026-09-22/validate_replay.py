"""Check replay timing, independent fee arithmetic and the strongest saved routes."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);args=ap.parse_args()
base=Path(__file__).parent
sys.path.insert(0,str(args.repo.resolve()))
from arbitrage_v2.evidence import utc
from arbitrage_v2.discovery import snapshot,_inputs
from arbitrage_v2.depth import book
from arbitrage_v2.prediction import steam_net,dmarket_net

p=json.loads((base/'results.json').read_text(encoding='utf-8'))
e=json.loads(gzip.decompress((base/'evidence.json.gz').read_bytes()))
cfg=p['protocol'];end=utc(cfg['outcome_end']);freeze=utc(cfg['freeze_at'])
assert utc(e['steam_snapshot']['retrieved_at'])<=freeze
assert 0<=(freeze-utc(e['steam_snapshot']['published_at'])).total_seconds()<=cfg['bulk_max_age_seconds']
assert all(0<=(freeze-utc(i['observed_at'])).total_seconds()<=cfg['bulk_max_age_seconds'] for i in e['catalogue'].values())
index={}
for c in e['captures']:
    assert freeze<=utc(c['retrieved_at'])<=end
    assert utc(c['_recorded_at'])<=end
    index.setdefault(c['title'],{})[c['kind']]=c
sf=p['policy']['steam_fee'];df=p['policy']['dmarket_fee']
def sfee(n):return max(sf['minimum_steam_cents'],n*sf['steam_bps']//10000)+max(sf['minimum_game_cents'],n*sf['game_bps']//10000)
def srecv(g):return max((n for n in range(g+1) if n+sfee(n)<=g),default=0)
def drecv(g):return max(0,g-max(df['minimum_cents'],-(-g*df['bps']//10000)))
for price in range(1,1001):
    assert srecv(price)==steam_net(price,sf['steam_bps'],sf['game_bps'],sf['minimum_steam_cents'],sf['minimum_game_cents'])
    assert drecv(price)==dmarket_net(price,df['bps'],df['minimum_cents'])

def units(rows,descending=False,limit=10000):
    out=[]
    for r in sorted(rows,key=lambda r:r['price_cents'],reverse=descending):
        out.extend([r['price_cents']]*min(r['quantity'],limit-len(out)))
        if len(out)==limit:break
    return out

for route in p['best_bid_routes']:
    aa=snapshot(None,route['item_a'],cfg['outcome_end'],cfg['detail_max_age_seconds'],index=index)
    bb=snapshot(None,route['item_b'],cfg['outcome_end'],cfg['detail_max_age_seconds'],index=index)
    a=_inputs(aa,('dmarket_ask','steam_bid'));b=_inputs(bb,('steam_ask','dmarket_bid'))
    q=route['quantity_a'];entry=sum(units(book(a,'dmarket_ask'))[:q])
    receipt=sum(srecv(g) for g in units(book(a,'steam_bid'),True)[:q])
    asks=units(book(b,'steam_ask'));bids=units(book(b,'dmarket_bid'),True)
    spent=0;n=0
    for g in asks[:len(bids)]:
        if spent+g>receipt:break
        spent+=g;n+=1
    returned=sum(drecv(g) for g in bids[:n])
    assert entry<=cfg['capital_cents']
    assert (entry,n,returned-entry-p['policy']['other_cost_cents'],receipt-spent)==(
        route['entry_cost_cents'],route['quantity_b'],route['predicted_net_cents'],route['steam_wallet_residual_cents'])

files=['prediction.py','discovery.py','depth.py','capture_selection.py','identity.py','money.py']
result=dict(checks=['All hints predate the evaluation window and satisfy age limits.',
    'All detailed captures fall inside the evaluation window.',
    'Steam and DMarket fees match independent integer arithmetic for 1..1000 cents.',
    'Ten strongest buy-order route estimates match independent unit-by-unit depth and budget arithmetic.'],
    module_sha256={n:hashlib.sha256((args.repo/'arbitrage_v2'/n).read_bytes()).hexdigest() for n in files},
    evidence_sha256=hashlib.sha256((base/'evidence.json.gz').read_bytes()).hexdigest())
(base/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
