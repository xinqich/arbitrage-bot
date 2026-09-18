"""Base-value CS2 identities. Individual premiums are never inferred."""
from decimal import Decimal, InvalidOperation


def valid_title(title):
    return (isinstance(title, str) and title == title.strip() and 0 < len(title) <= 300
            and not any(ord(c) < 32 for c in title))


def general_order(attributes):
    return isinstance(attributes, dict) and all(
        key in {'floatPartValue', 'paintSeed', 'phase'} and value == 'any'
        for key, value in attributes.items())


def base_offer(attributes, title):
    """Decorations are allowed at base value. A reported float must match wear."""
    ranges = {'Factory New': ('0', '.07'), 'Minimal Wear': ('.07', '.15'),
              'Field-Tested': ('.15', '.38'), 'Well-Worn': ('.38', '.45'),
              'Battle-Scarred': ('.45', '1')}
    wear = next((key for key in ranges if title.endswith(' ('+key+')')), None)
    if wear is None:
        return True
    try:
        value = Decimal(str(attributes.get('cs2', {}).get('float')))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('invalid_skin_float')
    low, high = map(Decimal, ranges[wear])
    return value.is_finite() and low <= value and (value < high or wear == 'Battle-Scarred' and value == high)


def known_item(journal, title, db=None):
    """Manual purchases use a previously observed exact Steam identity."""
    if not valid_title(title):
        return False
    with (journal.connect() if db is None else _borrow(db)) as connection:
        rows = connection.execute("SELECT payload FROM records WHERE category='capture' "
            "AND json_extract(payload,'$.kind')='details' AND json_extract(payload,'$.app_id')=730 "
            "AND json_extract(payload,'$.title')=? ORDER BY seq DESC", (title,))
        import json
        for row in rows:
            c = json.loads(row[0])
            steam = (c.get('payload') or {}).get('result', {})
            item, flags = steam.get('item', {}), steam.get('meta', {}).get('flags', {})
            if (not c.get('error') and c.get('status') == 200 and item.get('appId') == 730
                    and item.get('marketName') == title and type(flags.get('commodity')) is bool
                    and flags.get('marketable') is not False and flags.get('tradable') is not False):
                return True
    return False


from contextlib import contextmanager
@contextmanager
def _borrow(db):
    yield db
