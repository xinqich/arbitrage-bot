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


def request(cursor='', titles=None, page_size=100):
    return dict(provider='dmarket', kind='catalogue', app_id=730,
                title='CS2 catalogue' if titles is None else 'CS2 price refresh',
                cursor=cursor, titles=titles, page_size=page_size)


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
    size = job.get('page_size',100)
    if type(size) is not int or not 1 <= size <= 100:
        raise ValueError('invalid catalogue page size')
    body = {'filter': {'game': 'a8db'}, 'limit': str(size), 'cursor': cursor}
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
    """Incremental DMarket projection, invalidated only by relevant records."""
    with journal.connect() as db:
        condition="((category='capture' AND json_extract(payload,'$.kind')='catalogue' AND json_extract(payload,'$.provider')='dmarket') OR category IN ('catalogue_navigation','catalogue_progress'))"
        end=db.execute('SELECT coalesce(max(seq),0) FROM records').fetchone()[0]
        cached=getattr(journal,'_catalogue_cache',None)
        if cached and cached[0]==end:
            return cached[1]
        old=cached[1] if cached else {}
        items=dict(old.get('items',{}));cursor=old.get('cursor','')
        completed=old.get('last_complete_pass');latest_error=old.get('error')
        visited=set(old.get('visited_cursors',[]))
        progress=old.get('progress',{'selection_count':0,'checked':{}})
        rows=db.execute('SELECT id,category,payload FROM records WHERE '+condition+' AND seq>? ORDER BY seq',
                        (cached[0] if cached else 0,))
        changed=False
        for identifier,category,encoded in rows:
            changed=True
            c=json.loads(encoded)
            if category=='catalogue_progress':
                progress=c;continue
            if category=='catalogue_navigation':
                cursor=c['cursor'];visited=set();latest_error=c.get('error');continue
            if c.get('input_kind')!='recorded':
                continue
            if c.get('status')!=200 or c.get('error') or not c.get('payload'):
                latest_error=c.get('error') or 'catalogue_unavailable';continue
            latest_error=None;p=c['payload']
            for item in p['items']:
                items[item['title']]=dict(item,evidence_id=identifier)
            if p.get('requested_titles') is None:
                visited.add(p.get('request_cursor',cursor))
                cursor=p['next_cursor']
                if not cursor:
                    completed=c['retrieved_at'];visited=set()
    if cached and not changed:
        journal._catalogue_cache=(end,old)
        return old
    result=dict(items=items,cursor=cursor,visited_cursors=sorted(visited),
                last_complete_pass=completed,error=latest_error,progress=progress)
    journal._catalogue_cache=(end,result)
    return result


def collect_pages(journal,batch,config):
    """One bounded pass segment. Never restart an ended pass in the same run."""
    requests=[]
    already=sum(r['provider']=='dmarket' and r['kind']=='catalogue' for r in batch.results)
    for _ in range(max(0,config['catalogue_pages_per_run']-already)):
        if batch.stopped():break
        state=view(journal);cursor=state['cursor']
        if cursor in state['visited_cursors']:
            journal.append('catalogue_navigation',dict(at=batch.clock(),cursor='',error='repeated_catalogue_cursor'))
            break
        job=request(cursor,page_size=config['catalogue_page_size']);requests.append(job)
        before=state
        batch.ensure([job])
        if not batch.completed([job]):break
        after=view(journal)
        if after is before:
            # A custom/failed transport did not save a usable page.
            break
        if not after['cursor']:break
        if after['cursor'] in after['visited_cursors']:
            journal.append('catalogue_navigation',dict(at=batch.clock(),cursor='',error='repeated_catalogue_cursor'))
            break
    return requests


def research_roster(journal, seeds, capital, settings, slots, index, at, refresh_seconds=3600, exclude=(),
                    config=None, policy=None, with_audit=False, should_stop=None):
    from .screening import select
    chosen,count,audit=select(journal,view(journal),seeds,capital,settings,slots,index,at,
                             refresh_seconds,exclude,config,policy,should_stop)
    return (chosen,count,audit) if with_audit else (chosen,count)


def combined_items(journal, seeds=()):
    from . import csgotrader
    items=dict(view(journal)['items'])
    for title in csgotrader.view(journal)['names'] | {r['title'] for r in seeds}:
        items.setdefault(title,dict(app_id=730,title=title,dmarket_ask_cents=None,dmarket_bid_cents=None))
    return items


def coverage(journal, snapshots, eligible_titles=None, at=None, config=None):
    from . import csgotrader
    from .collection_settings import settings
    cfg=settings(config);at=at or stamp(datetime.now(timezone.utc))
    state=view(journal);steam=csgotrader.hints(journal,at,cfg['screening_max_age_seconds'])['status']
    names=set(state['items'])|csgotrader.view(journal)['names']|set(eligible_titles or [])
    checked=sum(bool(s['books'].get('dmarket_ask') and (s['books'].get('steam_bid') or s['books'].get('steam_ask')))
                or bool(s['books'].get('steam_ask') and (s['books'].get('dmarket_bid') or s['books'].get('dmarket_ask')))
                for s in snapshots)
    dm_fresh=sum(csgotrader.fresh(at,r.get('observed_at'),cfg['screening_max_age_seconds']) for r in state['items'].values())
    return dict(catalogue_size=len(set(state['items'])|csgotrader.view(journal)['names']),checked_items=checked,
        pending_items=max(0,len(names)-checked),
        last_complete_catalogue_pass=state['last_complete_pass'],catalogue_error=state['error'],
        coverage='partial',note='Best among freshly checked items; the entire market is not checked at once.',
        steam_bulk_access='public_screening_names_only' if not steam['prices_usable'] else 'public_screening_hints',
        catalogue_source='dmarket_and_csgotrader',screening=dict(steam=steam,
            dmarket=dict(names=len(state['items']),fresh_items=dm_fresh,stale_items=len(state['items'])-dm_fresh),
            max_age_seconds=cfg['screening_max_age_seconds']),
        latest_bulk_at=max((r['observed_at'] for r in state['items'].values()),default=None))
