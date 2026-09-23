"""Additional CLI commands for collection, predictions, and manual lifecycle events."""
from datetime import datetime,timezone
import json
from pathlib import Path
import time
from .evidence import read_json,stamp
from .journal import Journal
from .collector import credentials,collect_once,quota_status,history_points,history_summary
from .prediction import screen,FAMILY
from .routes import open_route,add_event,route_status,train,refine
from .returns import review_returns
from .collection_lock import collection_lock

COMMANDS={"journal-init","quota","collect","screen","history","route-open","route-event","routes","route-returns","return-review","train","refine"}

def register(parser,commands):
    parser.add_argument("--journal",default="data/research.sqlite3")
    commands.add_parser("journal-init")
    quota=commands.add_parser("quota")
    quota.add_argument("--env-file",type=Path)
    collect=commands.add_parser("collect")
    collect.add_argument("watchlist",type=Path)
    collect.add_argument("--env-file",type=Path)
    collect.add_argument("--request-budget",type=int,default=12)
    collect.add_argument("--steam-budget",type=int,default=4)
    collect.add_argument("--steam-source",choices=["steam_public","steamapis"],
                         help="override the watchlist's Steam source")
    collect.add_argument("--watch",action="store_true")
    collect.add_argument("--interval-seconds",type=int,default=3600)
    collect.add_argument("--max-cycles",type=int,default=0)
    collect.add_argument("--screen-policy",type=Path)
    evaluate=commands.add_parser("screen")
    evaluate.add_argument("watchlist",type=Path)
    evaluate.add_argument("--policy",type=Path,required=True)
    evaluate.add_argument("--as-of")
    evaluate.add_argument("--capital-cents",type=int,default=1000)
    evaluate.add_argument("--max-age-seconds",type=int,default=14400)
    evaluate.add_argument("--mode",choices=["paper","confirmed"],default="paper")
    history=commands.add_parser("history")
    history.add_argument("title")
    history.add_argument("--provider",choices=["steam_public","steamapis"])
    history.add_argument("--input-kind",choices=["recorded","synthetic"])
    history.add_argument("--summary",action="store_true")
    enter=commands.add_parser("route-open")
    enter.add_argument("route_id")
    enter.add_argument("prediction_id")
    enter.add_argument("--mode",choices=["paper","confirmed"],required=True)
    enter.add_argument("--at")
    event=commands.add_parser("route-event")
    event.add_argument("file",type=Path)
    routes=commands.add_parser("routes")
    routes.add_argument("--as-of")
    returns=commands.add_parser("route-returns",help="review return items using a route's recorded Steam Wallet")
    returns.add_argument("route_id")
    returns.add_argument("watchlist",type=Path)
    returns.add_argument("--policy",type=Path,required=True)
    returns.add_argument("--as-of")
    returns.add_argument("--max-age-seconds",type=int,default=14400)
    returns.add_argument("--remaining-cost-cents",type=int,
                         help="explicit estimate of additional costs from this point; omission means unknown")
    saved_review=commands.add_parser("return-review",help="read a saved historical return review")
    saved_review.add_argument("review_id")
    learn=commands.add_parser("train")
    learn.add_argument("--family",default=FAMILY)
    learn.add_argument("--mode",choices=["paper","confirmed"],required=True)
    learn.add_argument("--input-kind",choices=["synthetic","recorded"],required=True)
    learn.add_argument("--at")
    adjust=commands.add_parser("refine")
    adjust.add_argument("prediction_id")
    adjust.add_argument("model_id")
    adjust.add_argument("--mode",choices=["paper","confirmed"],required=True)
    adjust.add_argument("--at")

def now():
    return stamp(datetime.now(timezone.utc))

def run(args):
    journal=Journal(args.journal)
    if args.command=="journal-init":
        journal.initialize()
        return {"journal":str(journal.path)}
    if args.command=="quota":
        return quota_status(credentials(args.env_file))
    if args.command=="collect":
        with collection_lock(journal.path):
            return collect_loop(args,journal)
    if args.command=="screen":
        report=screen(journal,read_json(args.watchlist.read_bytes()),
                      read_json(args.policy.read_bytes()),args.as_of or now(),
                      args.capital_cents,args.max_age_seconds,args.mode)
        return dict(report,predictions=report["predictions"][:10],
                    prediction_count=len(report["predictions"]))
    if args.command=="history":
        fn=history_summary if args.summary else history_points
        return fn(journal,args.title,args.provider,args.input_kind)
    if args.command=="route-open":
        return open_route(journal,args.route_id,args.prediction_id,args.mode,args.at or now())
    if args.command=="route-event":
        return {"event_id":add_event(journal,read_json(args.file.read_bytes()))}
    if args.command=="routes":
        return [route_status(journal,r["route_id"],args.as_of or now()) for r in journal.records("route")]
    if args.command=="route-returns":
        return review_returns(journal,args.route_id,read_json(args.watchlist.read_bytes()),
                              read_json(args.policy.read_bytes()),args.as_of or now(),
                              args.max_age_seconds,args.remaining_cost_cents)
    if args.command=="return-review":
        return dict(journal.get(args.review_id,"return_review"),review_id=args.review_id)
    if args.command=="train":
        identifier=train(journal,args.family,args.mode,args.input_kind,args.at or now())
        return dict(journal.get(identifier,"model"),model_id=identifier)
    identifier=refine(journal,args.prediction_id,args.model_id,args.mode,args.at or now())
    return dict(journal.get(identifier,"prediction"),prediction_id=identifier)


def collect_loop(args,journal):
    if args.interval_seconds<60 or args.max_cycles<0:
        raise ValueError("interval must be >=60 seconds and max-cycles nonnegative")
    watchlist=read_json(args.watchlist.read_bytes())
    keys=credentials(args.env_file)
    source=args.steam_source or watchlist.get("steam_source","steamapis")
    cycle=0
    while True:
        quota={'status':'checked_by_collection' if source=='steamapis' else 'not_used','source':source}
        captures=collect_once(journal,watchlist,keys,args.request_budget,args.steam_budget,source)
        result={"cycle":cycle+1,"captures":captures,"provider_quota":quota}
        if args.screen_policy:
            report=screen(journal,watchlist,read_json(args.screen_policy.read_bytes()),now())
            result["screen"]=dict(report,predictions=report["predictions"][:10],
                                  prediction_count=len(report["predictions"]))
        cycle+=1
        if (not args.watch or not captures or
                (args.max_cycles and cycle>=args.max_cycles) or
                any(c["status"] in {401,403,429} or c.get("error") for c in captures)):
            return result
        print(json.dumps(result,indent=2,ensure_ascii=False),flush=True)
        time.sleep(args.interval_seconds)
