"""Bounded read-only market capture. No account or trading endpoints."""
from datetime import datetime, timezone
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import uuid4

from .evidence import stamp
from .collection_transport import prepare_request, retry_after, read_response

GAME_IDS={730:"a8db",570:"9a92",440:"tf2",252490:"rust"}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def credentials(env_file=None):
    values = {}
    if env_file:
        # Explicit optional source; never persisted or implicitly taken from v1.
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key,value=line.split("=",1)
                if key.strip() in {"STEAMAPIS_KEY","DMARKET_PUBLIC_KEY","DMARKET_SECRET_KEY","CSFLOAT_API_KEY"}:
                    values[key.strip()]=value.strip().strip("\"'")
    return {key: os.environ.get(key,values.get(key,"")) for key in
            ("STEAMAPIS_KEY","DMARKET_PUBLIC_KEY","DMARKET_SECRET_KEY","CSFLOAT_API_KEY")}

def sign(secret, decoded_path, query, timestamp):
    from nacl.signing import SigningKey
    seed=bytes.fromhex(secret)
    if len(seed)==64:
        seed=seed[:32]
    if len(seed)!=32:
        raise ValueError("invalid DMarket signing key length")
    message=("GET"+decoded_path+("?" + query if query else "")+str(timestamp)).encode()
    return "dmar ed25519 "+SigningKey(seed).sign(message).signature.hex()

def request_spec(provider, kind, app_id, title, keys):
    if app_id not in GAME_IDS or not isinstance(title,str) or not title.strip():
        raise ValueError("unsupported game or empty item title")
    headers={"Accept":"application/json","User-Agent":"arbitrage-v2-research/0.2"}
    if provider=="steamapis" and kind=="details":
        if not keys.get("STEAMAPIS_KEY"):
            raise ValueError("missing STEAMAPIS_KEY")
        path=f"/v2/steam/items/{app_id}/{quote(title,safe='')}"
        headers["x-api-key"]=keys["STEAMAPIS_KEY"]
        return "https://api.steamapis.com"+path,headers
    if provider!="dmarket" or kind not in {"offers","targets","fees"}:
        raise ValueError("endpoint is outside the read-only allowlist")
    if not keys.get("DMARKET_PUBLIC_KEY") or not keys.get("DMARKET_SECRET_KEY"):
        raise ValueError("missing DMarket credentials")
    game=GAME_IDS[app_id]
    query=""
    if kind=="targets":
        decoded=f"/marketplace-api/v1/targets-by-title/{game}/{title}"
        transmitted=f"/marketplace-api/v1/targets-by-title/{game}/{quote(title,safe='')}"
    elif kind=="offers":
        decoded=transmitted="/marketplace-api/v2/offers"
        query=urlencode({"gameId":game,"title":title,"limit":20,
                         "orderBy":"price","orderDir":"asc"},quote_via=quote)
    else:
        decoded=transmitted="/exchange/v1/customized-fees"
        query=urlencode({"gameId":game,"offerType":"dmarket","limit":100,"offset":0})
    now=int(time.time())
    headers.update({"X-Api-Key":keys["DMARKET_PUBLIC_KEY"],"X-Sign-Date":str(now),
                    "X-Request-Sign":sign(keys["DMARKET_SECRET_KEY"],decoded,query,now)})
    return "https://api.dmarket.com"+transmitted+("?" + query if query else ""),headers

def sanitize(value):
    if isinstance(value,dict):
        return {key:("[REDACTED]" if re.sub(r"[^a-z]","",key.lower()) in
                     {"apikey","token","authorization","cookie","secret","secretkey","tradetoken"}
                     else sanitize(item)) for key,item in value.items()}
    if isinstance(value,list):
        return [sanitize(item) for item in value]
    return value

def capture(journal,provider,kind,app_id,title,keys,opener=None):
    url,headers=request_spec(provider,kind,app_id,title,keys)
    identifier=str(uuid4())
    started=stamp(datetime.now(timezone.utc))
    timeout = prepare_request(journal, dict(provider=provider, kind=kind, app_id=app_id, title=title), identifier)
    status=None
    error=None
    payload=None
    retry_after_value=None
    try:
        with (opener or build_opener(NoRedirect())).open(Request(url,headers=headers),timeout=timeout) as response:
            status=response.status
            body=read_response(response,4_000_001)
            if len(body)>4_000_000:
                raise ValueError("response too large")
            payload=sanitize(json.loads(body,parse_float=str))
    except HTTPError as exc:
        status=exc.code
        retry_after_value=retry_after(exc.headers)
        error="http_"+str(exc.code)  # No response bodies, request headers, or secret-bearing URLs.
    except (URLError,TimeoutError,OSError) as exc:
        error="network_"+type(exc).__name__
    except (ValueError,UnicodeError):
        error="invalid_json_or_size"
    record={"provider":provider,"kind":kind,"app_id":app_id,"title":title,
            "started_at":started,"retrieved_at":stamp(datetime.now(timezone.utc)),
            "status":status,"error":error,"retry_after":retry_after_value,"payload":payload,"input_kind":"recorded",
            "archive_format":"sanitized_json_decimal_strings_v1"}
    record_id=journal.append("capture",record,identifier="capture:"+identifier)
    return dict(record_id=record_id,provider=provider,kind=kind,title=title,status=status,error=error,retry_after=retry_after_value)

def collect_once(journal,watchlist,keys,request_budget=12,steam_budget=4,steam_source=None,should_stop=None,
                 config=None,clock=None,wait=None):
    """Explicit one-shot CLI caps are optional invocation bounds, not lifetime stops.

    Use the worker's pacing, deadline and persistent cooldowns. The local desk
    owns the same OS collection lock as the CLI.
    """
    from .collection_batch import CollectionBatch, request_for, saved_state
    if type(request_budget) is not int or not 1<=request_budget<=100:
        raise ValueError('request_budget must be 1-100')
    if type(steam_budget) is not int or not 0<=steam_budget<=request_budget:
        raise ValueError('invalid Steam request budget')
    rows=watchlist.get('items')
    if not isinstance(rows,list) or not 1<=len(rows)<=30:
        raise ValueError('watchlist must contain 1-30 exact items')
    watchlist=dict(watchlist,steam_source=steam_source or watchlist.get('steam_source','steamapis'))
    plan=[];steam_used=0
    for item in rows:
        if set(item)!={'app_id','title'}:
            raise ValueError('invalid watchlist item')
        for kind in ('details','offers','targets'):
            request=request_for(watchlist,item['app_id'],item['title'],kind)
            if kind=='details':
                if steam_used>=steam_budget:
                    continue
                steam_used+=1
            if len(plan)<request_budget:
                plan.append(request)
    batch=CollectionBatch(journal,watchlist,config or {},keys,
        saved_state(journal).get('source_states',{}),
        clock or (lambda: stamp(datetime.now(timezone.utc))),should_stop or (lambda:False),wait=wait)
    batch.ensure(plan)
    return batch.results


def free_access(journal, keys):
    """Verify overage is off before increasing use of the optional keyed service.

    No inferred monthly allowance, paid-plan activation or account changes.
    The account response itself is never stored.
    """
    if not keys.get('STEAMAPIS_KEY'):
        raise ValueError('missing STEAMAPIS_KEY')
    timeout=prepare_request(journal,dict(provider='steamapis',kind='account',app_id=730,title='Account access check'))
    try:
        request=Request('https://api.steamapis.com/v2/account',headers={
            'x-api-key':keys['STEAMAPIS_KEY'],'Accept':'application/json'})
        with build_opener(NoRedirect()).open(request,timeout=timeout) as response:
            body=read_response(response,200001)
            if len(body)>200000:
                raise ValueError('oversize account response')
            result=json.loads(body).get('result',{})
        return dict(status=200,no_overage=result.get('overageEnabled') is False,
                    error=None if result.get('overageEnabled') is False else 'free_usage_not_verified')
    except HTTPError as exc:
        return dict(status=exc.code,error='http_'+str(exc.code),retry_after=retry_after(exc.headers))
    except (URLError,TimeoutError,OSError) as exc:
        return dict(status=None,error='network_'+type(exc).__name__)
    except (ValueError,TypeError,AttributeError):
        return dict(status=200,error='free_usage_not_verified')


def quota_status(keys):
    """Small provider account check; retain only quota facts, never account payloads."""
    headers={"x-api-key":keys.get("STEAMAPIS_KEY",""),"Accept":"application/json",
             "User-Agent":"arbitrage-v2-research/0.2"}
    try:
        with build_opener(NoRedirect()).open(Request("https://api.steamapis.com/v2/account",
                                                     headers=headers),timeout=20) as response:
            body=read_response(response,200001)
            if len(body)>200000:
                return {"status":"unknown"}
            data=json.loads(body).get("result",{})
        sub=data.get("subscriptions",{}).get("EXTRA_REQUESTS",{})
        if sub.get("planId")!="endpoint-none":
            return {"status":"unknown_plan"}
        usage=sub.get("usage",{})
        if not isinstance(usage,dict) or any(type(v) is not int or v<0 for v in usage.values()):
            return {"status":"unknown_usage"}
        return {"status":"known","included_remaining":max(0,500-sum(usage.values())),
                "overage_enabled":data.get("overageEnabled")}
    except (HTTPError,URLError,TimeoutError,OSError,ValueError):
        return {"status":"unavailable"}

def history_points(journal,title,provider=None,input_kind=None):
    """Keep the latest reported version per timestamp, without inventing daily coverage."""
    versions={}
    for record in journal.records("capture"):
        if record["kind"]!="details" or record["title"]!=title or record["error"]:
            continue
        source=record.get("provider","unspecified")
        partition=record.get("input_kind","unspecified")
        if (provider is not None and source!=provider) or (input_kind is not None and partition!=input_kind):
            continue
        result=(record.get("payload") or {}).get("result",{})
        for point in (result.get("priceHistory") or {}).get("data",[]):
            key=(source,partition,record["app_id"],point.get("date"))
            versions[key]={"app_id":record["app_id"],"reported_point":point,
                           "provider":source,"input_kind":partition,
                           "capture_id":record["record_id"],"retrieved_at":record["retrieved_at"]}
    return {"title":title,"points":list(versions.values()),"coverage":"unknown",
            "semantics":"provider_history_points_not_individual_confirmed_fills"}

def history_summary(journal,title,provider=None,input_kind=None):
    history=history_points(journal,title,provider,input_kind)
    groups={}
    for row in history["points"]:
        key=(row["provider"],row["input_kind"],row["app_id"])
        groups.setdefault(key,[]).append(row["reported_point"])
    summaries=[]
    for (source,partition,app_id),points in sorted(groups.items()):
        points.sort(key=lambda point:point["date"])
        summaries.append({"provider":source,"input_kind":partition,"app_id":app_id,
            "point_count":len(points),"first_point_at":points[0]["date"],
            "last_point_at":points[-1]["date"],"latest_point":points[-1]})
    return {"title":title,"sources":summaries,"coverage":"unknown",
            "semantics":history["semantics"],
            "note":"Timestamp span does not prove uninterrupted coverage; source counts are not added together."}
