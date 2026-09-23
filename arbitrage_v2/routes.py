"""Manual route journal. Only terminal resolution produces P&L or prediction errors."""
from datetime import timedelta,datetime,timezone
from fractions import Fraction
from contextlib import nullcontext
from .evidence import utc, stamp
from .prediction import cents,may_use_balance

STAGES={"entered","awaiting_transfer","steam_locked","awaiting_steam_sale",
        "steam_wallet","return_item_locked","awaiting_dmarket_sale","awaiting_settlement","awaiting_csfloat_sale","csfloat_pending","awaiting_payout"}
ACCOUNTS={"dmarket_regular","dmarket_tradable","steam_wallet","external_cost",
          "csfloat_deposited","csfloat_spendable","csfloat_pending","csfloat_withdrawable","cash_received"}


def account_rules(pred):
    purpose=pred.get("purpose","grow")
    dm={"dmarket_regular","dmarket_tradable"}
    if purpose=="grow":
        return dm,dm,{"steam_wallet"},dm|{"steam_wallet","external_cost"}
    if purpose=="start":
        source={"csfloat_deposited","csfloat_spendable"}
        return source,dm,source|{"steam_wallet"},source|dm|{"steam_wallet","external_cost"}
    if purpose=="withdraw":
        intermediate={"csfloat_pending","csfloat_spendable","csfloat_withdrawable","steam_wallet"}
        return dm,{"cash_received"},intermediate,dm|intermediate|{"cash_received","external_cost"}
    if purpose=="recovery":
        intermediate={"csfloat_pending","csfloat_spendable","csfloat_withdrawable","steam_wallet"}
        return set(),dm|{"cash_received"},intermediate,dm|intermediate|{"cash_received","external_cost"}
    raise ValueError("unsupported route purpose")


def _text(value):
    if not isinstance(value,str) or not value.strip():
        raise ValueError("nonempty identifier/reference required")
    return value

def open_route(journal,route_id,prediction_id,mode,entered_at,db=None):
    _text(route_id)
    if mode not in {"paper","confirmed"}:
        raise ValueError("mode must be paper or confirmed")
    with (journal.connect(True) if db is None else nullcontext(db)) as db:
        prediction=journal.get(prediction_id,"prediction",db)
        if utc(entered_at)<utc(prediction["predicted_at"]):
            raise ValueError("prediction cannot postdate entry")
        recorded=db.execute("SELECT recorded_at FROM records WHERE id=?",(prediction_id,)).fetchone()[0]
        if prediction["input_kind"]!="synthetic" and utc(entered_at)<utc(recorded):
            raise ValueError("prediction must actually be recorded before entry")
        if mode=="confirmed" and utc(entered_at)>datetime.now(timezone.utc):
            raise ValueError("confirmed entry cannot be in the future")
        if mode=="confirmed" and prediction["input_kind"]!="recorded":
            raise ValueError("synthetic predictions cannot be confirmed routes")
        if prediction.get("learning_mode",mode)!=mode:
            raise ValueError("paper and confirmed calibration cannot be mixed")
        value={"route_id":route_id,"prediction_id":prediction_id,"mode":mode,
               "entered_at":stamp(utc(entered_at))}
        journal.append("route",value,"route:"+route_id,db)
        return route_status(journal,route_id,entered_at,db)

def _state(journal,route_id,db=None):
    route=journal.get("route:"+route_id,"route",db)
    prediction=journal.get(route["prediction_id"],"prediction",db)
    events=[e for e in journal.records("route_event",db) if e["route_id"]==route_id]
    corrections=[c for c in journal.records("event_correction",db) if c["route_id"]==route_id]
    for correction in corrections:
        for event in events:
            if event["event_id"]==correction["target_event_id"]:
                event.update(correction["replacement"])
                event["record_id"]="event:"+event["event_id"]+":"+correction["record_id"]
    movements={a:0 for a in ACCOUNTS}
    assets={}
    stage="entered"
    eligible=None
    last=utc(route["entered_at"])
    resolution=None
    for event in events:
        last=utc(event["at"])
        if event["kind"]=="progress":
            stage=event["stage"]
            eligible=event["next_eligible_at"]
        elif event["kind"]=="movement":
            movements[event["account"]]+=event["net_delta_cents"]
        elif event["kind"]=="asset":
            key=event["asset_key"]
            assets[key]=assets.get(key,0)+event["quantity_delta"]
        elif event["kind"] in {"resolve","cancel"}:
            resolution=event
    if corrections:
        last=max(last,*(utc(c["at"]) for c in corrections))
    return route,prediction,events,movements,assets,stage,eligible,last,resolution

def add_event(journal,event,db=None):
    expected_base={"event_id","route_id","at","kind","reference"}
    kinds={
        "progress":{"stage","next_eligible_at","note"},
        "movement":{"account","net_delta_cents","fee_cents"},
        "asset":{"asset_key","quantity_delta"},
        "resolve":{"resolution","residual_disposition","pending_operations","confirmed"},
        "cancel":{"reason","confirmed"},
    }
    kind=event.get("kind")
    if not isinstance(kind,str) or kind not in kinds or set(event)!=expected_base|kinds[kind]:
        raise ValueError("invalid route-event contract")
    for name in ("event_id","route_id","reference"):
        _text(event[name])
    event=dict(event,at=stamp(utc(event["at"])))
    with (journal.connect(True) if db is None else nullcontext(db)) as db:
        # Retry the same already-recorded event even after route closure.
        existing=db.execute("SELECT 1 FROM records WHERE id=?",("event:"+event["event_id"],)).fetchone()
        if existing:
            journal.append("route_event",event,"event:"+event["event_id"],db)
            return event["event_id"]
        route,pred,events,movements,assets,stage,eligible,last,resolved=_state(journal,event["route_id"],db)
        if resolved:
            raise ValueError("route already resolved")
        sources,destinations,pending_accounts,allowed_accounts=account_rules(pred)
        if route["mode"]=="confirmed" and utc(event["at"])>datetime.now(timezone.utc):
            raise ValueError("confirmed event cannot be in the future")
        if utc(event["at"])<last:
            raise ValueError("route events must be chronological")
        if kind in {"movement","asset"}:
            discriminator="account" if kind=="movement" else "asset_key"
            for old in journal.records("route_event",db):
                if (old["kind"]==kind and old["reference"]==event["reference"]
                        and old.get(discriminator)==event.get(discriminator)):
                    old_route=journal.get("route:"+old["route_id"],"route",db)
                    if old_route["mode"]==route["mode"]:
                        raise ValueError("transaction reference already recorded in this mode")
        if route["mode"]=="confirmed" and kind in {"movement","asset"}:
            if any(r["reference"]==event["reference"] for r in journal.records("steam_wallet_record",db)):
                raise ValueError("This reference already belongs to a Steam wallet record.")
            if any(r["reference"]==event["reference"] for r in journal.records("real_funding",db)):
                raise ValueError("Outside funding cannot also be recorded as a route receipt.")
        if kind=="progress":
            if event["stage"] not in STAGES:
                raise ValueError("unknown route stage")
            if event["next_eligible_at"] is not None:
                utc(event["next_eligible_at"])
        elif kind=="movement":
            if event["account"] not in allowed_accounts or type(event["net_delta_cents"]) is not int:
                raise ValueError("invalid net cash movement")
            cents(abs(event["net_delta_cents"]))
            cents(event["fee_cents"])
            if event["account"]=="external_cost" and event["net_delta_cents"]>0:
                raise ValueError("external funding is not route income")
            if event["account"] in {"steam_wallet","csfloat_pending","csfloat_withdrawable","cash_received"} and movements[event["account"]]+event["net_delta_cents"]<0:
                raise ValueError("route cannot spend unrecorded Steam Wallet or pending/payout funds")
        elif kind=="asset":
            _text(event["asset_key"])
            if type(event["quantity_delta"]) is not int or event["quantity_delta"]==0:
                raise ValueError("whole nonzero item movement required")
            cents(abs(event["quantity_delta"]))
            if assets.get(event["asset_key"],0)+event["quantity_delta"]<0:
                raise ValueError("route cannot dispose of an unrecorded item")
        elif kind=="cancel":
            _text(event["reason"])
            if route["mode"]!="confirmed" or event["confirmed"] is not True or stage!="entered" or any(e["kind"] in {"movement","asset"} for e in events):
                raise ValueError("Only an unused real plan can be cancelled; acquired routes need a resolved outcome.")
        else:
            if event["resolution"] not in {"completed","liquidated","write_off"}:
                raise ValueError("unsupported resolution")
            if event["confirmed"] is not True or type(event["pending_operations"]) is not int or event["pending_operations"]!=0:
                raise ValueError("resolution requires explicit confirmation and no pending operations")
            if event["residual_disposition"] not in {"none","written_off"}:
                raise ValueError("residual holdings must be cleared or explicitly written off")
            if any(movements[a]!=0 for a in pending_accounts if a.startswith("csfloat_") and a not in sources):
                raise ValueError("CSFloat credits or payout funds are still pending")
            if (any(assets.values()) or movements["steam_wallet"]!=0) and event["residual_disposition"]!="written_off":
                raise ValueError("unresolved inventory or Wallet remainder")
            if pred.get("purpose")!="recovery" and not any(e["kind"]=="movement" and e["account"] in sources
                       and e["net_delta_cents"]<0 for e in events):
                raise ValueError("resolution requires a recorded source-market acquisition debit")
            if event["resolution"]=="completed" and (any(assets.values()) or movements["steam_wallet"]!=0):
                raise ValueError("completed cycles cannot hide residuals; use liquidation/write_off")
            net=sum(movements[a] for a in allowed_accounts if a!="steam_wallet")
            duration=int((utc(event["at"])-utc(route["entered_at"])).total_seconds())
            actual_cost=-sum(e["net_delta_cents"] for e in events if e["kind"]=="movement"
                             and e["account"] in sources and e["net_delta_cents"]<0)
            receipts=sum(e["net_delta_cents"] for e in events if e["kind"]=="movement"
                         and e["account"] in destinations and e["net_delta_cents"]>0)
            steam_receipts=sum(e["net_delta_cents"] for e in events if e["kind"]=="movement"
                               and e["account"]=="steam_wallet" and e["net_delta_cents"]>0)
            return_spend=-sum(e["net_delta_cents"] for e in events if e["kind"]=="movement"
                              and e["account"]=="steam_wallet" and e["net_delta_cents"]<0)
            if event["resolution"]=="completed":
                conversions_required=pred.get("route_variant")!="direct_transfer" and pred.get("purpose")!="recovery"
                if not (receipts>0 and (not conversions_required or (steam_receipts>0 and return_spend>0))):
                    raise ValueError("a completed route requires its planned Steam conversions and destination proceeds")
                if eligible is not None and utc(event["at"])<utc(eligible):
                    raise ValueError("route still has a recorded eligibility delay")
            outcome={"route_id":route["route_id"],"prediction_id":route["prediction_id"],
                     "mode":route["mode"],"input_kind":pred["input_kind"],"family":pred["family"],
                     "resolved_at":event["at"],"resolution":event["resolution"],
                     "actual_net_cents":net,"actual_entry_cost_cents":actual_cost,
                     "actual_duration_seconds":duration,
                     "net_error_cents":None if pred["predicted_net_cents"] is None else net-pred["predicted_net_cents"],
                     "entry_cost_error_cents":None if pred.get("purpose")=="recovery" else actual_cost-pred["entry_cost_cents"],
                     "dmarket_receipts_error_cents":None if pred.get("purpose","grow") in {"withdraw","recovery"} else receipts-pred["predicted_dmarket_receipts_cents"],
                     "destination_receipts_error_cents":None if pred.get("purpose")=="recovery" else receipts-pred.get("predicted_destination_receipts_cents",pred["predicted_dmarket_receipts_cents"]),
                     "purpose":pred.get("purpose","grow"),"destination":pred.get("destination","dmarket"),
                     "parent_route_id":pred.get("parent_route_id"),
                     "steam_receipts_error_cents":None if pred.get("purpose")=="recovery" else steam_receipts-pred["steam_proceeds_cents"],
                     "return_purchase_error_cents":None if pred.get("purpose")=="recovery" else return_spend-pred["return_purchase_cents"],
                     "duration_error_seconds":None if pred["predicted_duration_seconds"] is None
                         else duration-pred["predicted_duration_seconds"],
                     "fee_cents_reported":sum(e["fee_cents"] for e in events if e["kind"]=="movement"),
                     "balance_changes":movements,"confirmation":"paper_simulated" if route["mode"]=="paper" else "operator_recorded",
                     "engine_version":next((r["engine_version"] for r in reversed(journal.records("paper_settings",db))
                         if r["route_id"]==route["route_id"]),pred.get("engine_version","legacy-manual-v1"))}
            journal.append("outcome",outcome,"outcome:"+route["route_id"],db)
        journal.append("route_event",event,"event:"+event["event_id"],db)
        if route["mode"]=="confirmed" and journal.records("real_funding",db):
            from .real_funds import assert_cash
            assert_cash(journal,db)
    return event["event_id"]

def _return_review_summary(journal,route_id,events,now,resolved,db=None):
    reviews=[r for r in journal.records("return_review",db) if r["route_id"]==route_id
             and utc(r["evaluated_at"])<=now and utc(r["_recorded_at"])<=now]
    if not reviews:
        return None
    review=reviews[-1]
    options=review["options"]
    changed=review["route_event_ids"]!=[event["record_id"] for event in events]
    expired=any(now>utc(option["snapshot"]["quote_expires_at"]) for option in options)
    evidence_ids={identifier for option in options for identifier in option["snapshot"]["evidence_ids"]}
    titles={option["item_b"] for option in options}|{item["title"] for item in review["excluded"]}
    newer=any(c["record_id"] not in evidence_ids and c.get("title") in titles
              and c.get("app_id")==730 and c.get("kind") in {"details","targets"}
              and utc(review["_recorded_at"])<utc(c["_recorded_at"])<=now
              and utc(c["retrieved_at"])<=now for c in journal.records("capture",db))
    return {"review_id":review["record_id"],"evaluated_at":review["evaluated_at"],
            "status":review["status"],"option_count":len(options),
            "wallet_available_cents_at_review":review["wallet_available_cents"],
            "route_state_changed":changed,"quotes_expired":expired,"newer_market_evidence":newer,
            "refresh_required":bool(resolved) or changed or expired or newer}

def route_status(journal,route_id,as_of,db=None):
    route,pred,events,movements,assets,stage,eligible,last,resolved=_state(journal,route_id,db)
    now=utc(as_of)
    if now<last:
        raise ValueError("status time precedes recorded events")
    duration=pred["predicted_duration_seconds"]
    due=utc(route["entered_at"])+timedelta(seconds=duration) if duration is not None else None
    # Inventory and cash movements are operational facts. No open-route P&L is computed.
    return {"route_id":route_id,"mode":route["mode"],"prediction":pred,
            "state":("cancelled" if resolved["kind"]=="cancel" else "resolved") if resolved else stage,"open_assets":assets if not resolved else {},
            "steam_wallet_available_cents":movements["steam_wallet"] if not resolved else 0,
            "account_movements":movements if not resolved else {},
            "written_off_holdings":assets if resolved and resolved.get("residual_disposition")=="written_off" else {},
            "written_off_wallet_cents":movements["steam_wallet"] if resolved and resolved.get("residual_disposition")=="written_off" else 0,
            "latest_return_review":_return_review_summary(journal,route_id,events,now,resolved,db),
            "next_eligible_at":eligible if not resolved else None,
            "overdue":not resolved and due is not None and now>due,
            "eligibility_reached":not resolved and eligible is not None and now>=utc(eligible),
            "completion_time_unknown":duration is None,
            "balance_type_rules":{balance:{action:may_use_balance(balance,pred["app_id"],action)
                for action in ("market_purchase","target_purchase","withdraw")}
                for balance in ("regular","tradable")},
            "actual_profit":journal.get("outcome:"+route_id,"outcome",db) if resolved and resolved["kind"]=="resolve" else None}

def train(journal,family,mode,input_kind,trained_at,engine_version=None):
    utc(trained_at)
    samples=[]
    for outcome in journal.records("outcome"):
        if (outcome["family"]==family and outcome["mode"]==mode and outcome["input_kind"]==input_kind
                and (engine_version is None or outcome.get("engine_version","legacy-manual-v1")==engine_version)
                and utc(outcome["resolved_at"])<=utc(trained_at)
                and utc(outcome["_recorded_at"])<=utc(trained_at)):
            samples.append(outcome)
    if not samples:
        raise ValueError("no resolved outcomes for this family and evidence mode")
    bias=Fraction(0)
    for outcome in samples:
        pred=journal.get(outcome["prediction_id"],"prediction")
        # Compare with the unadjusted baseline to avoid recursively compounding corrections.
        if pred["base_net_cents"] is None:
            raise ValueError("cannot calibrate a prediction with unknown costs")
        bias+=Fraction(outcome["actual_net_cents"]-pred["base_net_cents"],pred["entry_cost_cents"])
    if len({o.get("purpose","grow") for o in samples})!=1:
        raise ValueError("route purposes require separate learning families")
    engines={o.get("engine_version","legacy-manual-v1") for o in samples}
    if len(engines)!=1:
        raise ValueError("choose a single paper engine version before training")
    model={"engine_version":next(iter(engines)),"family":family,"mode":mode,"input_kind":input_kind,"trained_at":stamp(utc(trained_at)),
           "sample_count":len(samples),"return_bias_bps":int(bias*10000/len(samples)),
           "duration_mean_seconds":sum(o["actual_duration_seconds"] for o in samples)//len(samples),
           "outcome_ids":[o["record_id"] for o in samples],"algorithm":"resolved-error-mean-v1"}
    return journal.append("model",model)

def refine(journal,prediction_id,model_id,mode,at):
    base=journal.get(prediction_id,"prediction")
    model=journal.get(model_id,"model")
    if utc(at)<utc(model["trained_at"]) or utc(at)<utc(base["predicted_at"]):
        raise ValueError("future information cannot refine an earlier prediction")
    if (base["family"],mode,base["input_kind"])!=(model["family"],model["mode"],model["input_kind"]):
        raise ValueError("model family/evidence partition mismatch")
    if base.get("engine_version","legacy-manual-v1")!=model.get("engine_version","legacy-manual-v1"):
        raise ValueError("model engine partition mismatch")
    result=dict(base,predicted_at=stamp(utc(at)),model_version=model_id,
                training_sample_count=model["sample_count"],
                predicted_net_cents=base["base_net_cents"]+
                    base["entry_cost_cents"]*model["return_bias_bps"]//10000,
                predicted_duration_seconds=max(base["minimum_known_delay_seconds"],model["duration_mean_seconds"]),
                parent_prediction_id=prediction_id,learning_mode=mode)
    return journal.append("prediction",result)


def correct_event(journal,correction):
    """Replace a mistaken amount in an OPEN route's projection; retain both records."""
    required={"correction_id","route_id","target_event_id","at","reference","reason","value","fee_cents"}
    if set(correction)!=required:
        raise ValueError("invalid correction fields")
    for key in ("correction_id","route_id","target_event_id","reference","reason"):
        _text(correction[key])
    if type(correction["value"]) is not int:
        raise ValueError("whole corrected quantity or net cents required")
    cents(abs(correction["value"]))
    cents(correction["fee_cents"])
    with journal.connect(True) as db:
        route,pred,events,movements,assets,stage,eligible,last,resolved=_state(journal,correction["route_id"],db)
        target=journal.get("event:"+correction["target_event_id"],"route_event",db)
        if target["route_id"]!=route["route_id"] or target["kind"] not in {"movement","asset"}:
            raise ValueError("choose a money or item update in this route")
        original_amount=target["net_delta_cents"] if target["kind"]=="movement" else target["quantity_delta"]
        if target["event_id"].startswith("real-step:") and original_amount * correction["value"] < 0:
            raise ValueError("A purchase or sale correction cannot reverse its direction. Record a refund or separate operation instead.")
        replacement={"net_delta_cents":correction["value"],"fee_cents":correction["fee_cents"]} if target["kind"]=="movement" else {"quantity_delta":correction["value"]}
        if target["kind"]=="asset" and (not correction["value"] or correction["fee_cents"]):
            raise ValueError("item corrections need a nonzero quantity and zero fee")
        record=dict(correction,replacement=replacement,schema_version=1)
        identifier="correction:"+correction["correction_id"]
        if db.execute("SELECT 1 FROM records WHERE id=?",(identifier,)).fetchone():
            journal.append("event_correction",record,identifier,db)
            return identifier
        if resolved:
            raise ValueError("resolved results are frozen; record a separate linked recovery instead")
        if utc(correction["at"])<last or (route["mode"]=="confirmed" and utc(correction["at"])>datetime.now(timezone.utc)):
            raise ValueError("correction time must follow recorded state and cannot be in the future for real routes")
        journal.append("event_correction",record,identifier,db)
        corrected=_state(journal,route["route_id"],db)[2]
        cash={a:0 for a in ACCOUNTS};inventory={}
        for e in corrected:
            if e["kind"]=="asset":
                key=e["asset_key"];inventory[key]=inventory.get(key,0)+e["quantity_delta"]
                if inventory[key]<0:
                    raise ValueError("correction would sell items that were never received")
            if e["kind"]=="movement":
                key=e["account"];cash[key]+=e["net_delta_cents"]
                if key=="external_cost" and e["net_delta_cents"]>0:
                    raise ValueError("external funding is not route income")
                if key in {"steam_wallet","csfloat_pending","csfloat_withdrawable","cash_received"} and cash[key]<0:
                    raise ValueError("correction would spend money before it was received")
        if route["mode"]=="confirmed" and journal.records("real_funding",db):
            from .real_funds import assert_cash
            assert_cash(journal,db)
    return identifier
