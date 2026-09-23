"""User-reconciled full Steam wallet. Separate from route spending limits and P&L."""
from .evidence import utc
from .prediction import cents
from .real_funds import real_time, text_field, reference_available
from .routes import _state


def records(journal, db=None):
    rows=journal.records('steam_wallet_record',db)
    corrections={}
    for correction in journal.records('steam_wallet_correction',db):
        corrections[correction['wallet_id']]=correction
    for row in rows:
        if row['wallet_id'] in corrections:
            row['amount_cents']=corrections[row['wallet_id']]['amount_cents']
    return rows


def status(journal, db=None):
    if db is None:
        with journal.connect() as connection:
            connection.execute('BEGIN')
            return status(journal,connection)
    rows=records(journal,db)
    snapshots=[(utc(r['at']),i,r) for i,r in enumerate(rows) if r['kind']=='balance']
    if not snapshots:
        return {'configured':False, 'balance_cents':None, 'records':rows, 'needs_reconciliation':False}
    _,_,baseline=max(snapshots)
    since=utc(baseline['at'])
    total=baseline['amount_cents']
    for row in rows:
        if row['kind']=='adjustment' and utc(row['at'])>since:
            total+=row['amount_cents']
    links={(r['route_id'],r['reference']) for r in journal.records('recovery_link',db)}
    for route in journal.records('route',db):
        if route['mode']!='confirmed':
            continue
        for event in _state(journal,route['route_id'],db)[2]:
            if (event['kind']=='movement' and event['account']=='steam_wallet' and utc(event['at'])>since
                    and (route['route_id'],event['reference']) not in links):
                total+=event['net_delta_cents']
    return {'configured':True, 'balance_cents':total, 'as_of':baseline['at'], 'records':rows,
            'needs_reconciliation':total<0,
            'note':'Full wallet from your last balance record and later real transactions. Route budgets stay separate.'}


def _amount(kind,value):
    if type(value) is not int:
        raise ValueError('Use whole cents.')
    cents(abs(value))
    if kind=='balance' and value<0 or kind=='adjustment' and value==0:
        raise ValueError('A balance cannot be negative; an adjustment must be nonzero.')


def record(journal,request):
    if set(request)!={'wallet_id','kind','at','reference','amount_cents'} or request['kind'] not in {'balance','adjustment'}:
        raise ValueError('Invalid Steam wallet record.')
    for key in ('wallet_id','reference'):
        text_field(request[key])
    _amount(request['kind'],request['amount_cents'])
    value=dict(request,at=real_time(request['at']),schema_version=1,mode='confirmed')
    identifier='steam-wallet:'+request['wallet_id']
    with journal.connect(True) as db:
        if db.execute('SELECT 1 FROM records WHERE id=?',(identifier,)).fetchone():
            return journal.append('steam_wallet_record',value,identifier,db)
        reference_available(journal,value['reference'],db)
        if any(r['reference']==value['reference'] for r in records(journal,db)):
            raise ValueError('This Steam wallet reference is already recorded.')
        if value['kind']=='adjustment' and not any(r['kind']=='balance' and utc(r['at'])<utc(value['at']) for r in records(journal,db)):
            raise ValueError('Record an earlier Steam wallet balance before an outside adjustment.')
        journal.append('steam_wallet_record',value,identifier,db)
    return identifier


def correct(journal,request):
    if set(request)!={'correction_id','wallet_id','at','reason','amount_cents'}:
        raise ValueError('Invalid Steam wallet correction.')
    for key in ('correction_id','wallet_id','reason'):
        text_field(request[key])
    value=dict(request,at=real_time(request['at']),schema_version=1)
    identifier='steam-wallet-correction:'+request['correction_id']
    with journal.connect(True) as db:
        if db.execute('SELECT 1 FROM records WHERE id=?',(identifier,)).fetchone():
            return journal.append('steam_wallet_correction',value,identifier,db)
        target=journal.get('steam-wallet:'+request['wallet_id'],'steam_wallet_record',db)
        _amount(target['kind'],request['amount_cents'])
        dates=[utc(target['at'])]+[utc(r['at']) for r in journal.records('steam_wallet_correction',db) if r['wallet_id']==request['wallet_id']]
        if utc(value['at'])<max(dates):
            raise ValueError('A correction must follow its original record and earlier corrections.')
        journal.append('steam_wallet_correction',value,identifier,db)
    return identifier
