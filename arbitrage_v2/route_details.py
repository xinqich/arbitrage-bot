"""Read-only detail from the prediction's saved evidence, never today's replacement quotes."""
from urllib.parse import quote, urlencode
from .prediction import _steam_book
from .holding_costs import route_cost
from .routes import route_status


def detail(journal, prediction_id=None, route_id=None, at=None):
    if bool(prediction_id)==bool(route_id):
        raise ValueError('Choose exactly one prediction or route.')
    with journal.connect() as db:
        db.execute('BEGIN')
        route=journal.get('route:'+route_id,'route',db) if route_id else None
        prediction_id=route['prediction_id'] if route else prediction_id
        prediction=journal.get(prediction_id,'prediction',db)
        captures=[]
        for identifier in prediction.get('evidence_ids',[]):
            try:
                captures.append(dict(journal.get(identifier,'capture',db),record_id=identifier))
            except ValueError:
                pass
        items={}
        for leg in ('a','b'):
            title=prediction['item_'+leg]
            app=prediction.get('item_'+leg+'_identity',{}).get('app_id',prediction.get('app_id',730))
            data={'title':title,'app_id':app,'game':'CS2' if app==730 else str(app),
                  'steam_lowest_ask_cents':None,'steam_highest_bid_cents':None,'history':None,
                  'links':{'steam':f'https://steamcommunity.com/market/listings/{app}/'+quote(title,safe=''),
                           'dmarket':'https://dmarket.com/ingame-items/item-list/csgo?'+urlencode({'exchangeTab':'exchange','title':title}) if app==730 else None}}
            cap=next((c for c in captures if c.get('title')==title and c.get('app_id')==app
                      and c.get('kind')=='details' and not c.get('error')),None)
            if cap:
                result=(cap.get('payload') or {}).get('result',{})
                for side,key in (('ask','steam_lowest_ask_cents'),('bid','steam_highest_bid_cents')):
                    try:
                        data[key]=_steam_book(cap,result,side)['price_cents']
                    except (ValueError,TypeError,KeyError):
                        pass
                points=(result.get('priceHistory') or {}).get('data',[])
                dates=sorted({str(r['date'])[:10] for r in points if isinstance(r,dict) and r.get('date')})
                data['history']={'reported_dates':len(dates),'provider':cap['provider'],
                    'retrieved_at':cap['retrieved_at'],'evidence_id':cap['record_id'],
                    'note':'Reported chart activity; not verified daily sale totals.'} if dates else None
            items[leg]=data
        result={'prediction_id':prediction_id,'prediction':prediction,'items':items,
                'holding_cost':route_cost(journal,route_id,db) if route else None}
        if route:
            result['route']=route_status(journal,route_id,at,db)
        return result
