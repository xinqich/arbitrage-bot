"""Exact conditional route economics; observed quotes do not reserve future exits."""
from datetime import datetime, timezone, timedelta
from fractions import Fraction
from itertools import product
from .evidence import utc, stamp
from .money import exact_integer, price_cents
from .depth import levels, book, total, quote, ENGINE

FAMILY="cs2_standard_case_cycle_v1"
ITEM_FAMILY="cs2_standard_item_cycle_v2"
QUALIFIED_ITEMS={"Paris 2023 Legends Sticker Capsule":True,"AK-47 | Slate (Field-Tested)":False}


def qualified_identity(title,commodity):
    return (title.endswith(" Case") and commodity is True) or (title in QUALIFIED_ITEMS and commodity is QUALIFIED_ITEMS[title])


def cents(value):
    if type(value) is not int or value<0:
        raise ValueError("expected nonnegative integer cents")
    return exact_integer(value)

def steam_net(gross, steam_bps, game_bps, minimum_steam, minimum_game):
    """Seller cents for ONE item's buyer-price ceiling, in one-cent currency.

    Invert separately floored Steam/publisher fees on seller proceeds, each
    with its configured minimum. Multiply this result by item quantity only
    afterwards: twelve 66c CS2 sales yield 12 * 59c, not net(12 * 66c).
    USD 5%/10% reference: docs/STEAM_FEE_AUDIT.md. This is quote arithmetic,
    not a substitute for the actual transaction's confirmed net receipt.
    """
    for value in (gross,steam_bps,game_bps,minimum_steam,minimum_game):
        cents(value)
    if steam_bps>=10000 or game_bps>=10000:
        raise ValueError("invalid fee rate")
    low,high=0,gross
    while low<=high:
        net=(low+high)//2
        total=net+max(minimum_steam,net*steam_bps//10000)+max(minimum_game,net*game_bps//10000)
        if total<=gross:
            low=net+1
        else:
            high=net-1
    return max(0,high)

def dmarket_net(gross,bps,minimum=0):
    for value in (gross,bps,minimum):
        cents(value)
    if bps>=10000:
        raise ValueError("invalid fee rate")
    return max(0,gross-max(minimum,(gross*bps+9999)//10000))

def may_use_balance(balance_type,app_id,action):
    if balance_type=="regular":
        return action in {"market_purchase","target_purchase","withdraw"}
    if balance_type=="tradable":
        return app_id==730 and action=="market_purchase"
    raise ValueError("unknown balance type")

def _top(rows,side,unit,price_key="price",quantity_key="quantity"):
    parsed=[]
    for row in rows:
        price=price_cents(row[price_key],currency="USD",unit=unit)
        quantity=exact_integer(row[quantity_key])
        if price>0 and quantity>0:
            parsed.append((price,quantity))
    if not parsed:
        raise ValueError("no observed depth")
    best=(max if side=="bid" else min)(price for price,_ in parsed)
    # The top cumulative quantity is unambiguous; overlapping duplicate rows are not summed.
    return {"price_cents":best,"quantity":max(q for p,q in parsed if p==best)}

def _market_captures(journal,title,as_of,max_age_seconds,kinds,db=None):
    if type(max_age_seconds) is not int or max_age_seconds<0:
        raise ValueError("invalid freshness bound")
    now=utc(as_of)
    relevant={}
    for capture in journal.records("capture",db):
        if (capture["kind"] in kinds
                and capture["title"]==title and capture["app_id"]==730
                and utc(capture["retrieved_at"])<=now and utc(capture["_recorded_at"])<=now):
            previous=relevant.get(capture["kind"])
            if previous is None or utc(capture["retrieved_at"])>=utc(previous["retrieved_at"]):
                relevant[capture["kind"]]=capture
    if not set(kinds)<=relevant.keys():
        raise ValueError("missing_capture")
    for capture in relevant.values():
        if capture["error"] or capture["status"]!=200:
            raise ValueError("provider_request_failed")
        age=(now-utc(capture["retrieved_at"])).total_seconds()
        if not 0<=age<=max_age_seconds:
            raise ValueError("stale_capture")
    input_kinds={r.get("input_kind") for r in relevant.values()}
    if len(input_kinds)!=1 or not input_kinds<={"recorded","synthetic"}:
        raise ValueError("mixed_or_unknown_evidence_kind")
    return relevant,next(iter(input_kinds))

def _steam_observation(capture,title,as_of,max_age_seconds):
    steam=capture["payload"]["result"]
    if steam["item"]["appId"]!=730 or steam["item"].get("marketName")!=title:
        raise ValueError("Steam item identity mismatch")
    if not qualified_identity(title,steam.get("meta",{}).get("flags",{}).get("commodity")):
        raise ValueError("outside_standard_case_family")
    observed=utc(steam["histogram"]["date"])
    if not 0<=(utc(as_of)-observed).total_seconds()<=max_age_seconds:
        raise ValueError("stale_steam_book")
    return steam,observed

def _book(rows,side,unit,price_key="price",quantity_key="quantity",semantics="incremental"):
    parsed=levels(rows,side,unit,price_key,quantity_key,semantics)
    if not parsed:
        raise ValueError("no_observed_depth")
    return dict(parsed[0],levels=parsed)


def _steam_book(capture,steam,side):
    rows=steam["histogram"]["buyOrders" if side=="bid" else "sellOrders"]
    semantics=capture["payload"].get("provenance",{}).get("book_quantity_semantics")
    if semantics not in {"incremental","cumulative"}:
        # Existing providers without a qualified depth contract keep top-only support.
        return _top(rows,side,"usd")
    return _book(rows,side,"usd",semantics=semantics)


def _target_bid(capture,title):
    rows=[r for r in capture["payload"]["orders"]
          if r.get("title")==title and (r.get("attributes")=={} or
              (title in QUALIFIED_ITEMS and QUALIFIED_ITEMS[title] is False and
               r.get("attributes")=={"floatPartValue":"any","paintSeed":"any","phase":"any"}))]
    return _book(rows,"bid","cents","price","amount")


def steam_sale_snapshot(journal,title,as_of,max_age_seconds=14400,db=None):
    captures,input_kind=_market_captures(journal,title,as_of,max_age_seconds,("details",),db)
    capture=captures["details"]
    steam,observed=_steam_observation(capture,title,as_of,max_age_seconds)
    return {"title":title,"app_id":730,"steam_bid":_steam_book(capture,steam,"bid"),
        "evidence_ids":[capture["record_id"]],"input_kind":input_kind,"source_time":stamp(observed)}


def destination_snapshot(journal,title,as_of,max_age_seconds=14400,db=None):
    captures,input_kind=_market_captures(journal,title,as_of,max_age_seconds,("targets",),db)
    capture=captures["targets"]
    return {"title":title,"app_id":730,"dmarket_bid":_target_bid(capture,title),
        "evidence_ids":[capture["record_id"]],"input_kind":input_kind,
        "source_time":capture["retrieved_at"]}


def return_snapshot(journal,title,as_of,max_age_seconds,db=None):
    """Only the two books consumed by a Steam -> DMarket return are required."""
    captures,input_kind=_market_captures(journal,title,as_of,max_age_seconds,
                                        ("details","targets"),db)
    steam,observed=_steam_observation(captures["details"],title,as_of,max_age_seconds)
    return {"title":title,"app_id":730,
            "steam_ask":_steam_book(captures["details"],steam,"ask"),
            "dmarket_bid":_target_bid(captures["targets"],title),
            "evidence_ids":[captures[k]["record_id"] for k in ("details","targets")],
            "source_time":stamp(observed),"input_kind":input_kind,
            "quote_expires_at":stamp(min(observed, *(utc(c["retrieved_at"]) for c in captures.values()))
                                     + timedelta(seconds=max_age_seconds)),
            "history_coverage":"unknown"}

def _offer_ask(capture,title):
    offers={}
    for row in capture["payload"]["items"]:
        attr=row["attributes"]
        if (attr.get("title")==title and attr.get("gameId")=="a8db"
                and attr.get("withdrawable") is True and attr.get("tradable") is True
                and row.get("locked") is False):
            cs2=attr.get("cs2",{})
            if title in QUALIFIED_ITEMS and QUALIFIED_ITEMS[title] is False:
                if (cs2.get("stickers")!=[] or cs2.get("charms")!=[] or cs2.get("isProskin") is not False
                        or cs2.get("phase")!="" or cs2.get("rarePattern")!="RARE_PATTERN_UNSPECIFIED"):
                    continue
                from decimal import Decimal, InvalidOperation
                try:
                    value=Decimal(str(cs2.get("float","-1")))
                except InvalidOperation as exc:
                    raise ValueError("invalid_skin_float") from exc
                if not value.is_finite() or not Decimal("0.15")<=value<Decimal("0.38"):
                    continue
            price=cents(exact_integer(row["priceCents"]))
            if row["offerId"] in offers and offers[row["offerId"]]!=price:
                raise ValueError("conflicting_duplicate_offer")
            if price>0:
                offers[row["offerId"]]=price
    if not offers:
        raise ValueError("no_eligible_offers")
    best=min(offers.values())
    return {"price_cents":best,"quantity":sum(p==best for p in offers.values()),
            "levels":[{"price_cents":p,"quantity":1,"level_id":oid}
                      for oid,p in sorted(offers.items(),key=lambda x:(x[1],x[0]))]}


def market_snapshot(journal,title,as_of,max_age_seconds,db=None):
    relevant,input_kind=_market_captures(journal,title,as_of,max_age_seconds,
                                        ("details","offers","targets"),db)
    steam,observed=_steam_observation(relevant["details"],title,as_of,max_age_seconds)
    return {
        "title":title,"app_id":730,
        "dmarket_ask":_offer_ask(relevant["offers"],title),
        "steam_bid":_steam_book(relevant["details"],steam,"bid"),
        "steam_ask":_steam_book(relevant["details"],steam,"ask"),
        "dmarket_bid":_target_bid(relevant["targets"],title),
        "evidence_ids":[relevant[k]["record_id"] for k in ("details","offers","targets")],
        "source_time":stamp(observed),
        "history_rows":len((steam.get("priceHistory") or {}).get("data",[])),
        "history_coverage":"unknown",
        "input_kind":input_kind,
    }

LISTING_ENGINE = "listing-price-scenarios-v1"
MIDPOINT_ENGINE = "midpoint-price-scenarios-v1"


def _sale_estimate(rows,quantity,net,mode):
    if mode == "current_bids":
        return quote(rows,quantity,net)
    if mode not in {"listing_price", "midpoint"} or not rows:
        raise ValueError("unsupported_sale_scenario")
    # An ask supplies a price reference, never demand or a simulated fill.
    price=min(row["price_cents"] for row in rows)
    unit=net(price)
    return {"quantity":quantity,"gross_cents":cents(price*quantity),
        "net_cents":cents(unit*quantity),"fee_cents":cents((price-unit)*quantity),
        "complete":True,"fills":[],"quantity_is_assumed":True,
        "assumed_price_cents":price,"assumed_net_unit_cents":unit,
        "observed_listing_quantity_at_price":sum(r["quantity"] for r in rows if r["price_cents"]==price),
        "basis":mode,"sale_time":"unknown"}


def sale_quote(snapshot, venue, quantity, net, mode):
    if mode == "midpoint":
        bid = max(r["price_cents"] for r in book(snapshot, venue+"_bid"))
        ask = min(r["price_cents"] for r in book(snapshot, venue+"_ask"))
        if bid > ask:
            raise ValueError("midpoint_requires_consistent_bid_and_ask")
        result = _sale_estimate([{"price_cents":(bid+ask)//2,"quantity":0}], quantity, net, mode)
        result.update(observed_bid_cents=bid, observed_ask_cents=ask)
        return result
    return _sale_estimate(book(snapshot, venue+("_bid" if mode=="current_bids" else "_ask")), quantity, net, mode)


def calculate(a,b,quantity,policy,steam_sale_mode="current_bids",dmarket_sale_mode="current_bids"):
    if steam_sale_mode not in {"current_bids","listing_price","midpoint"} or dmarket_sale_mode not in {"current_bids","listing_price","midpoint"}:
        raise ValueError("unsupported_sale_scenario")
    if a["input_kind"]!=b["input_kind"]:
        raise ValueError("mixed_evidence_kinds")
    if type(quantity) is not int or quantity<=0:
        raise ValueError("positive whole-item quantity required")
    entry=quote(book(a,"dmarket_ask"),quantity)
    sf=policy["steam_fee"]
    sale=sale_quote(a,"steam",quantity,lambda p:steam_net(p,sf["steam_bps"],
        sf["game_bps"],sf["minimum_steam_cents"],sf["minimum_game_cents"]),steam_sale_mode)
    if not entry["complete"] or not sale["complete"]:
        raise ValueError("insufficient_entry_or_steam_sale_depth")
    spend,steam_receipt=entry["gross_cents"],sale["net_cents"]
    return_limit=total(book(b,"dmarket_bid")) if dmarket_sale_mode=="current_bids" else total(book(b,"steam_ask"))
    purchase=quote(book(b,"steam_ask"),return_limit,budget=steam_receipt)
    return_quantity=purchase["quantity"]
    if return_quantity==0:
        raise ValueError("no_affordable_supported_return")
    df=policy["dmarket_fee"]
    exit_quote=sale_quote(b,"dmarket",return_quantity,
                     lambda p:dmarket_net(p,df["bps"],df["minimum_cents"]),dmarket_sale_mode)
    returned=exit_quote["net_cents"]
    extra=policy["other_cost_cents"]
    if extra is not None:
        cents(extra)
    remaining=steam_receipt-purchase["gross_cents"]
    engine=MIDPOINT_ENGINE if "midpoint" in (steam_sale_mode,dmarket_sale_mode) else ENGINE if steam_sale_mode==dmarket_sale_mode=="current_bids" else LISTING_ENGINE
    return {
        "family":FAMILY if a["title"].endswith(" Case") and b["title"].endswith(" Case") else ITEM_FAMILY,
        "app_id":730,"item_a":a["title"],"item_b":b["title"],
        "item_a_identity":{"schema_version":1,"app_id":730,"market_hash_name":a["title"],"attributes":{}},
        "item_b_identity":{"schema_version":1,"app_id":730,"market_hash_name":b["title"],"attributes":{}},
        "quantity_a":quantity,"quantity_b":return_quantity,"entry_cost_cents":spend,
        "steam_proceeds_cents":steam_receipt,"return_purchase_cents":purchase["gross_cents"],
        "predicted_dmarket_receipts_cents":returned,"other_cost_cents":extra,
        "predicted_fee_cents":sale["fee_cents"]+exit_quote["fee_cents"],
        "engine_version":engine,"purpose":"grow","destination":"dmarket",
        "quote_legs":{"entry":entry,"steam_sale":sale,"return_purchase":purchase,"exit":exit_quote},
        "steam_wallet_residual_cents":remaining,
        "base_net_cents":None if extra is None else returned-spend-extra,
        "predicted_net_cents":None if extra is None else returned-spend-extra,
        "costs_known":extra is not None,
        "minimum_known_delay_seconds":policy["minimum_known_delay_seconds"],
        "base_duration_seconds":None,"predicted_duration_seconds":None,
        "estimate_type":"conditional_on_future_quotes_and_declared_costs" if engine==ENGINE else "conditional_midpoint_scenario" if engine==MIDPOINT_ENGINE else "conditional_listing_price_scenario",
        "sale_scenarios":{"steam":steam_sale_mode,"dmarket":dmarket_sale_mode},
        "uncertainty":{"sales_history":"unknown","future_demand":"unknown","sale_time":"unknown"},
        "model_version":engine,"training_sample_count":0,
        "evidence_ids":sorted(set(a["evidence_ids"]+b["evidence_ids"])),
        "input_kind":a["input_kind"] if a["input_kind"]==b["input_kind"] else "mixed",
        "policy":policy,"entry_recommendation":False,
    }

def screen(journal,watchlist,policy,as_of,capital_cents=1000,max_age_seconds=14400,mode="paper"):
    from .discovery import screen as discover
    return discover(journal,watchlist,policy,as_of,capital_cents,max_age_seconds,mode)
