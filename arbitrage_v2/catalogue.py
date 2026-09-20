"""Bounded public-market catalogue capture; summaries never prove fills."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from .collector import NoRedirect, sanitize
from .evidence import stamp, utc
from .collection_transport import prepare_request, retry_after, read_response
from .identity import valid_title
from .money import price_cents, exact_integer

PATH = '/marketplace-api/v1/aggregated-prices'


def request(cursor='', titles=None):
    return dict(provider='dmarket', kind='catalogue', app_id=730,
                title='CS2 catalogue' if titles is None else 'CS2 price refresh',
                cursor=cursor, titles=titles)


def parse(payload, at):
    if not isinstance(payload, dict) or not isinstance(payload.get('aggregatedPrices'), list):
        raise ValueError('invalid_catalogue_response')
    rows = []
    for raw in payload['aggregatedPrices']:
        title = raw.get('title')
        if not valid_title(title):
            raise ValueError('invalid_catalogue_identity')
        def price(key):
            v = raw.get(key)
            if not v or not v.get('Amount'):
                return None
            p = price_cents(v['Amount'], currency=v['Currency'], unit='cents')
            return p or None
        rows.append(dict(app_id=730, title=title, dmarket_ask_cents=price('offerBestPrice'),
            dmarket_bid_cents=price('orderBestPrice'), observed_at=at,
            offer_count=exact_integer(raw.get('offerCount','0')),
            order_count=exact_integer(raw.get('orderCount','0'))))
    cursor = payload.get('nextCursor', '')
    if not isinstance(cursor, str) or len(cursor) > 4096:
        raise ValueError('invalid_catalogue_cursor')
    return {'items': rows, 'next_cursor': cursor, 'source_time_kind': 'retrieval_time_only',
            'depth_qualification': 'summary_only_not_available_at_best_price'}


def fetch(journal, job, keys, opener=None):
    from nacl.signing import SigningKey
    if not keys.get('DMARKET_PUBLIC_KEY') or not keys.get('DMARKET_SECRET_KEY'):
        raise ValueError('missing DMarket credentials')
    titles, cursor = job.get('titles'), job.get('cursor','')
    if (titles is not None and (not isinstance(titles,list) or not 1 <= len(titles) <= 100
            or not all(valid_title(t) for t in titles))) or not isinstance(cursor,str) or len(cursor)>4096:
        raise ValueError('invalid catalogue request')
    body = {'filter': {'game': 'a8db'}, 'limit': '100', 'cursor': cursor}
    if titles is not None:
        body['filter']['titles'] = titles
    encoded = json.dumps(body,separators=(',',':')).encode()
    ts = str(int(time.time()))
    seed = bytes.fromhex(keys['DMARKET_SECRET_KEY'])[:32]
    sig = SigningKey(seed).sign(b'POST'+PATH.encode()+encoded+ts.encode()).signature.hex()
    headers = {'Content-Type':'application/json','Accept':'application/json',
        'X-Api-Key':keys['DMARKET_PUBLIC_KEY'],'X-Sign-Date':ts,'X-Request-Sign':'dmar ed25519 '+sig}
    identifier = uuid4().hex
    started = stamp(datetime.now(timezone.utc))
    timeout = prepare_request(journal, dict(provider='dmarket', kind='catalogue', app_id=730, title=job['title']), identifier)
    status, error, payload = None, None, None
    retry_after_value = None
    try:
        with (opener or build_opener(NoRedirect())).open(Request('https://api.dmarket.com'+PATH,
                headers=headers,data=encoded),timeout=timeout) as response:
            status=response.status; data=read_response(response,4_000_001)
            if len(data)>4_000_000:
                raise ValueError('catalogue_response_too_large')
            raw=sanitize(json.loads(data,parse_float=str))
            payload=parse(raw,stamp(datetime.now(timezone.utc)))
            payload.update(raw=raw,request_cursor=cursor,requested_titles=titles,
                           body_sha256=sha256(data).hexdigest())
    except HTTPError as exc:
        status,error=exc.code,'http_'+str(exc.code)
        retry_after_value=retry_after(exc.headers)
    except (URLError,TimeoutError,OSError) as exc:
        error='network_'+type(exc).__name__
    except (ValueError,KeyError,TypeError,UnicodeError):
        error='invalid_catalogue_contract'
    record=dict(provider='dmarket',kind='catalogue',app_id=730,title=job['title'],started_at=started,
        retrieved_at=stamp(datetime.now(timezone.utc)),status=status,error=error,retry_after=retry_after_value,payload=payload,
        input_kind='recorded',archive_format='catalogue-summary-v1')
    rid=journal.append('capture',record,'capture:'+identifier)
    return dict(record_id=rid,provider='dmarket',kind='catalogue',title=job['title'],status=status,error=error,retry_after=retry_after_value)


def view(journal):
    """Rebuild a compact projection once per call, caching by journal sequence."""
    with journal.connect() as db:
        end=db.execute('SELECT coalesce(max(seq),0) FROM records').fetchone()[0]
        cached=getattr(journal,'_catalogue_cache',None)
        if cached and cached[0]==end:
            return cached[1]
        rows=db.execute("SELECT id,payload FROM records WHERE category='capture' "
            "AND json_extract(payload,'$.kind')='catalogue' ORDER BY seq")
        items,cursor,completed,latest_error={},'',None,None
        for identifier,encoded in rows:
            c=json.loads(encoded)
            if c.get('input_kind')!='recorded':
                continue
            if c.get('status')!=200 or c.get('error') or not c.get('payload'):
                latest_error=c.get('error') or 'catalogue_unavailable'
                continue
            latest_error=None;p=c['payload']
            for item in p['items']:
                items[item['title']]=dict(item,evidence_id=identifier)
            if p.get('requested_titles') is None:
                cursor=p['next_cursor']
                if not cursor:
                    completed=c['retrieved_at']
        row=db.execute("SELECT payload FROM records WHERE category='catalogue_progress' ORDER BY seq DESC LIMIT 1").fetchone()
        progress=json.loads(row[0]) if row else {'selection_count':0,'checked':{}}
    result=dict(items=items,cursor=cursor,last_complete_pass=completed,error=latest_error,progress=progress)
    journal._catalogue_cache=(end,result)
    return result


def research_roster(journal, seeds, capital, settings, slots, index, at, refresh_seconds=3600, exclude=()):
    """Four promising selections, then one oldest check; deterministic on restart."""
    state=view(journal);rows=dict(state['items'])
    for item in seeds:
        rows.setdefault(item['title'],dict(item,dmarket_ask_cents=None,dmarket_bid_cents=None))
    def histogram(title):
        payload=index.get(title,{}).get('details',{}).get('payload')
        if not isinstance(payload,dict):return {}
        result=payload.get('result')
        if not isinstance(result,dict):return {}
        value=result.get('histogram')
        return value if isinstance(value,dict) else {}
    steam_ceiling=0;unknown_outward=False
    for title,row in rows.items():
        ask=row.get('dmarket_ask_cents')
        if ask is None or settings['minimum_purchase_cents']<=ask<=capital:
            hist=histogram(title)
            try:
                prices=[price_cents(r['price'],currency='USD',unit='usd') for side in ('buyOrders','sellOrders') for r in hist.get(side,[])]
                if ask and prices:
                    steam_ceiling=max(steam_ceiling,(capital//ask)*max(prices))
                else:unknown_outward=True
            except (ValueError,KeyError,TypeError):unknown_outward=True
    candidates=[]
    for title,row in rows.items():
        if title in exclude:
            continue
        ask=row.get('dmarket_ask_cents')
        levels=histogram(title).get('sellOrders',[])
        try:
            steam_ask=min((price_cents(r['price'],currency='USD',unit='usd') for r in levels),default=None)
        except (ValueError,KeyError,TypeError):
            steam_ask=None
        # Unknown Steam affordability stays eligible for exploration. A DMarket
        # price cannot rule out the return leg's Steam affordability.
        outward=ask is None or settings['minimum_purchase_cents']<=ask<=capital
        returning=steam_ask is None or (steam_ask>=settings['minimum_purchase_cents'] and (unknown_outward or steam_ask<=steam_ceiling))
        if not outward and not returning:
            continue
        score=0
        if ask and steam_ask:
            score=max(steam_ask/ask,(row.get('dmarket_bid_cents') or 0)/steam_ask)
        elif ask and ask<=capital:
            score=1
        checked=state['progress'].get('checked',{}).get(title)
        # Avoid spending a research request to refresh already fresh complete books.
        captures=index.get(title,{})
        fresh=len(captures)==3 and all(not c.get('error') and c.get('status')==200
            and 0<=(utc(at)-utc(c['retrieved_at'])).total_seconds()<refresh_seconds for c in captures.values())
        if fresh:
            try:
                fresh=0 <= (utc(at)-utc(histogram(title)['date'])).total_seconds() < refresh_seconds
            except (KeyError,ValueError,TypeError):
                fresh=False
        if not fresh:
            candidates.append((title,score,checked or ''))
    count=state['progress']['selection_count'];selected=[]
    for _ in range(min(slots,len(candidates))):
        count+=1
        choice=min(candidates,key=(lambda r:(r[2],r[0])) if count%5==0 else (lambda r:(-r[1],r[2],r[0])))
        selected.append({'app_id':730,'title':choice[0]});candidates.remove(choice)
    return selected,count


def coverage(journal, snapshots, eligible_titles=None):
    state=view(journal)
    checked=sum(bool(s['books'].get('dmarket_ask') and (s['books'].get('steam_bid') or s['books'].get('steam_ask')))
                or bool(s['books'].get('steam_ask') and (s['books'].get('dmarket_bid') or s['books'].get('dmarket_ask')))
                for s in snapshots)
    return dict(catalogue_size=len(state['items']),checked_items=checked,
        pending_items=max(0,len(set(state['items'])|set(eligible_titles or []))-checked),
        last_complete_catalogue_pass=state['last_complete_pass'],catalogue_error=state['error'],
        coverage='partial',note='Best among freshly checked items; the entire market is not checked at once.',
        steam_bulk_access='denied_in_2026_09_17_qualification',catalogue_source='dmarket',
        latest_bulk_at=max((r['observed_at'] for r in state['items'].values()),default=None))
