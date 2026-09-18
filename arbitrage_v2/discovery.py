"""Price opportunities independently of history and of the paper fill engine."""
from collections import Counter
from . import search_rules
from itertools import product

from .depth import ENGINE, book, total
from .evidence import stamp, utc
from .prediction import (FAMILY, ITEM_FAMILY, LISTING_ENGINE, MIDPOINT_ENGINE, _market_captures, _offer_ask,
    _steam_observation, _steam_book, _target_bid, calculate, cents)

DISCOVERY_VERSION = "cs2-catalogue-scenarios-v3"
SUPPORTED_DISCOVERY_VERSIONS = {"separate-sale-scenarios-v1", "separate-sale-scenarios-v2", DISCOVERY_VERSION}
BOOKS = {"steam_bid": "details", "steam_ask": "details",
         "dmarket_ask": "offers", "dmarket_bid": "targets"}
SALE_BOOKS = {venue:{"current_bids":(venue+"_bid",), "listing_price":(venue+"_ask",),
                     "midpoint":(venue+"_bid",venue+"_ask")} for venue in ("steam","dmarket")}


def sale_sources(source, names):
    rows = [_price_source(source["books"][name]) for name in names]
    return rows[0] if len(rows)==1 else {"basis":"midpoint", "sources":rows,
        "source_time":min(r["source_time"] for r in rows), "evidence_ids":sorted({r["evidence_id"] for r in rows})}



def _problem(title, name, exc):
    reason = str(exc)
    empty = reason in {"no observed depth", "no_observed_depth"}
    if empty:
        status = "no_observed_bids" if name.endswith("bid") else "no_observed_listings"
        message = ("No eligible buy orders were observed in this snapshot. A listing-price alternative may still be possible."
                   if name.endswith("bid") else "No eligible asking price was observed in this snapshot.")
    elif reason == "no_eligible_offers":
        status, message = "no_eligible_offers", "No matching unlocked, tradable and withdrawable offers were observed. This does not prove the item cannot sell."
    elif reason in {"stale_capture", "stale_steam_book"}:
        status, message = "needs_refresh", "This price is too old for a current estimate."
    else:
        status, message = "needs_data", "This price is missing, its source failed, or its format/identity cannot be used."
    return {"title": title, "book": name, "source_kind": BOOKS[name],
            "status": status, "reason": reason, "message": message}


def snapshot(journal, title, at, max_age_seconds=14400, db=None, index=None):
    result = {"title": title, "app_id": 730, "books": {}, "issues": []}
    for kind in ("details", "offers", "targets"):
        names = [name for name, source in BOOKS.items() if source == kind]
        try:
            captures, partition = _market_captures(journal, title, at, max_age_seconds, (kind,), db, index)
            capture = captures[kind]
            source_time = capture["retrieved_at"]
            if kind == "details":
                steam, observed = _steam_observation(capture, title, at, max_age_seconds)
                source_time = stamp(observed)
        except (ValueError, KeyError, TypeError) as exc:
            result["issues"].extend(_problem(title, name, exc) for name in names)
            continue
        # Parse each side separately. A missing bid is not a missing asking price.
        for name in names:
            try:
                value = (_steam_book(capture, steam, "bid" if name == "steam_bid" else "ask")
                         if kind == "details" else _offer_ask(capture, title)
                         if kind == "offers" else _target_bid(capture, title))
                result["books"][name] = {"value": value, "evidence_id": capture["record_id"],
                    "input_kind": partition, "source_time": source_time}
            except (ValueError, KeyError, TypeError) as exc:
                result["issues"].append(_problem(title, name, exc))
    return result


def _inputs(source, names):
    selected = {name: source["books"][name] for name in names}
    kinds = {row["input_kind"] for row in selected.values()}
    if len(kinds) != 1:
        raise ValueError("mixed_evidence_kinds")
    return {"title": source["title"], "app_id": source["app_id"],
        **{name: row["value"] for name, row in selected.items()},
        "input_kind": kinds.pop(), "evidence_ids": sorted({r["evidence_id"] for r in selected.values()}),
        "source_time": min(r["source_time"] for r in selected.values())}


def _price_source(row):
    return {key: value for key, value in row.items() if key != "value"}


def route_inputs(journal, pred, at, max_age_seconds=14400, db=None):
    """Revalidate only the books actually used by a new saved prediction."""
    a = snapshot(journal, pred["item_a"], at, max_age_seconds, db)
    b = a if pred["item_a"] == pred["item_b"] else snapshot(journal, pred["item_b"], at, max_age_seconds, db)
    scenarios = pred["sale_scenarios"]
    names_a = ("dmarket_ask", *SALE_BOOKS["steam"][scenarios["steam"]])
    names_b = ("steam_ask", *SALE_BOOKS["dmarket"][scenarios["dmarket"]])
    for source, names in ((a, names_a), (b, names_b)):
        for name in names:
            if name not in source["books"]:
                problem = next(i for i in source["issues"] if i["book"] == name)
                raise ValueError(problem["reason"])
    for venue, ref in pred.get('ranking_price_sources', {}).items():
        source = a if venue == 'steam' else b
        current = source['books'].get(venue+'_ask')
        if current is None or current['evidence_id'] != ref['evidence_id']:
            raise ValueError('ranking_comparison_changed_search_again')
    return _inputs(a, names_a), _inputs(b, names_b)


def screen(journal, watchlist, policy, as_of, capital_cents=1000, max_age_seconds=14400, mode="paper", settings=None):
    cents(capital_cents)
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        raise ValueError("invalid freshness bound")
    if type(policy["minimum_known_delay_seconds"]) is not int or policy["minimum_known_delay_seconds"] < 0:
        raise ValueError("invalid delay floor")
    if mode not in {"paper", "confirmed"}:
        raise ValueError("invalid search mode")
    settings = search_rules.validate(settings or search_rules.current(journal))
    snapshots, issues, predictions, limits = [], [], [], Counter()
    # One projection for the entire scan, not three full journal reads per item.
    index = capture_index(journal, as_of)
    quote_cache = {}
    seen = set()
    for item in watchlist["items"]:
        key = (item["app_id"], item["title"])
        if key in seen:
            continue
        seen.add(key)
        if item["app_id"] != 730:
            issues.append({"title": item["title"], "status": "unsupported_game",
                "reason": "unsupported_game", "message": "This game is not supported by the current CS2 calculations."})
            continue
        row = snapshot(journal, item["title"], as_of, max_age_seconds, index=index)
        snapshots.append(row)
        issues.extend(row["issues"])

    from .entry_depth import unused_entry
    from .routes import train
    models = {}
    for family, engine in product((FAMILY, ITEM_FAMILY), (ENGINE, LISTING_ENGINE, MIDPOINT_ENGINE)):
        try:
            model_id = train(journal, family, mode, "recorded", as_of, engine_version=engine)
            models[(family, engine)] = (model_id, journal.get(model_id, "model"))
        except ValueError as exc:
            if str(exc) != "no resolved outcomes for this family and evidence mode":
                raise
    for source_a in snapshots:
        for steam_mode, steam_books in SALE_BOOKS["steam"].items():
            if not {"dmarket_ask", *steam_books} <= source_a["books"].keys():
                continue
            try:
                a = _inputs(source_a, ("dmarket_ask", *steam_books))
                if mode == "paper":
                    a = unused_entry(journal, a)
            except (ValueError, KeyError, TypeError) as exc:
                issue = {"title": source_a["title"], "book": "dmarket_entry", "status": "entry_unavailable",
                         "reason": str(exc), "message": "This entry cannot currently use these offers in this funding pool."}
                if issue not in issues:
                    issues.append(issue)
                continue
            if a['dmarket_ask']['price_cents'] < settings['minimum_purchase_cents']:
                limits[(a['title'], '', steam_mode, '', 'purchase_below_minimum')] += 1
                continue
            upper = min(capital_cents // a["dmarket_ask"]["price_cents"], total(book(a, "dmarket_ask")), 1000)
            if steam_mode == "current_bids":
                upper = min(upper, total(book(a, steam_books[0])))
            if not upper:
                limits[(source_a["title"], "", steam_mode, "", "entry_exceeds_available_budget")] += 1
                continue
            for source_b, (dmarket_mode, dmarket_books) in product(snapshots, SALE_BOOKS["dmarket"].items()):
                if not {"steam_ask", *dmarket_books} <= source_b["books"].keys():
                    continue
                key = (a["title"], source_b["title"], steam_mode, dmarket_mode)
                try:
                    b = _inputs(source_b, ("steam_ask", *dmarket_books))
                except ValueError as exc:
                    limits[key + (str(exc),)] += 1
                    continue
                if b['steam_ask']['price_cents'] < settings['minimum_purchase_cents']:
                    limits[key + ('purchase_below_minimum',)] += 1
                    continue
                # Destination receipts increase with quantity inside each priority.
                # Retain the maximum affordable quantity and the narrow-spread boundary.
                # Smaller B quantities within either priority are safely dominated.
                return_limits = [None]
                ask_row = source_b['books'].get('dmarket_ask')
                if dmarket_mode == 'current_bids' and ask_row and ask_row['input_kind']==b['input_kind']:
                    ask = ask_row['value']['price_cents']
                    bids = book(b,'dmarket_bid')
                    if max(r['price_cents'] for r in bids) <= ask:
                        narrow = sum(r['quantity'] for r in bids
                            if (ask-r['price_cents'])*10000 < ask*settings['narrow_spread_bps'])
                        if 0 < narrow < total(bids):
                            return_limits.append(narrow)
                for quantity, return_limit in product(range(1, upper + 1), return_limits):
                    try:
                        pred = calculate(a, b, quantity, policy, steam_mode, dmarket_mode, return_limit, quote_cache)
                        if pred["entry_cost_cents"] > capital_cents:
                            limits[key + ("entry_exceeds_available_budget",)] += 1
                            continue
                    except ValueError as exc:
                        limits[key + (str(exc),)] += 1
                        continue
                    if return_limit is not None:
                        maximum = calculate(a,b,quantity,policy,steam_mode,dmarket_mode,None,quote_cache)
                        if maximum['quantity_b'] == pred['quantity_b']:
                            continue
                    search_rules.annotate(pred,source_a,source_b,settings)
                    # Ranking may compare an ask not used by the economic calculation.
                    pred['ranking_price_sources'] = {venue:_price_source(source['books'][venue+'_ask'])
                        for venue,source in (('steam',source_a),('dmarket',source_b))
                        if venue+'_ask' in source['books'] and source['books'][venue+'_ask']['input_kind']==pred['input_kind']}
                    pred['ranking_evidence_ids'] = sorted({r['evidence_id'] for r in pred['ranking_price_sources'].values()})
                    pred.update(return_quantity_limit=return_limit, predicted_at=stamp(utc(as_of)), discovery_version=DISCOVERY_VERSION, search_mode=mode,
                        price_sources={"entry": _price_source(source_a["books"]["dmarket_ask"]),
                                       "steam_sale": sale_sources(source_a, steam_books),
                                       "return_purchase": _price_source(source_b["books"]["steam_ask"]),
                                       "exit": sale_sources(source_b, dmarket_books)})
                    model_id, model = models.get((pred["family"], pred["engine_version"]), (None, None))
                    # Bid-fill outcomes do not calibrate a listing-price assumption.
                    if model and pred["input_kind"] == "recorded" and pred["base_net_cents"] is not None:
                        pred.update(model_version=model_id, learning_mode=mode, training_sample_count=model["sample_count"],
                            predicted_net_cents=pred["base_net_cents"]+pred["entry_cost_cents"]*model["return_bias_bps"]//10000,
                            predicted_duration_seconds=max(pred["minimum_known_delay_seconds"], model["duration_mean_seconds"]))
                        pred["uncertainty"]["sale_time"] = "estimated_from_resolved_routes"
                    predictions.append(pred)
    with journal.connect(True) as db:
        predictions = [dict(p,prediction_id=journal.append('prediction',p,db=db)) for p in predictions]
    predictions.sort(key=search_rules.sort_key)
    return {"family": FAMILY, "discovery_version": DISCOVERY_VERSION, "ranking_version":search_rules.VERSION, "search_settings":settings, "snapshots": snapshots,
        "excluded": issues, "predictions": predictions, "unsupported_scenarios": sum(limits.values()),
        "scenario_limits": [{"item_a": k[0], "item_b": k[1], "steam_sale": k[2], "dmarket_sale": k[3],
                             "reason": k[4], "quantities_affected": count} for k, count in limits.items()],
        "positive_conditional_scenarios": sum(p["predicted_net_cents"] is not None and p["predicted_net_cents"] > 0 for p in predictions),
        "economic_evidence": "not_established", "assumptions": policy,
        "search_limits": {"maximum_entry_quantity": 1000, "roster_items": len(seen), "price_max_age_seconds": max_age_seconds},
        "uncertainty_note": "Missing history does not exclude a route. Future prices, buyers and sale times are unknown. Listing estimates assume a sale at an observed asking price; listing quantities are not buyer demand."}


def capture_index(journal, as_of):
    result = {}
    at = utc(as_of)
    for c in journal.records('capture'):
        if (c.get('kind') not in {'details','offers','targets'} or c.get('app_id') != 730
                or utc(c['retrieved_at']) > at or utc(c['_recorded_at']) > at):
            continue
        item = result.setdefault(c['title'], {})
        old = item.get(c['kind'])
        if old is None or utc(old['retrieved_at']) <= utc(c['retrieved_at']):
            item[c['kind']] = c
    return result
