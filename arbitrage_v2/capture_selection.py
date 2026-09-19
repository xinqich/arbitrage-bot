"""Select quote captures without letting a failed fetch erase earlier evidence.

Selection does not refresh timestamps or validate prices. Callers still validate
identity, depth and age. A successful empty/invalid response supersedes older
prices, and recorded evidence can never fall back to synthetic evidence.
"""
from .evidence import utc


def select_captures(rows, as_of):
    at = utc(as_of)
    latest, successful = {}, {}
    for row in rows:
        if (utc(row['retrieved_at']) > at or utc(row['_recorded_at']) > at):
            continue
        key = (row['app_id'], row['title'], row['kind'])
        old = latest.get(key)
        if old is None or utc(row['retrieved_at']) >= utc(old['retrieved_at']):
            latest[key] = row
        partition = key + (row.get('input_kind'),)
        old = successful.get(partition)
        if (row.get('status') == 200 and not row.get('error')
                and (old is None or utc(row['retrieved_at']) >= utc(old['retrieved_at']))):
            successful[partition] = row
    result = {}
    for key, attempt in latest.items():
        selected = attempt
        if attempt.get('error') or attempt.get('status') != 200:
            selected = successful.get(key + (attempt.get('input_kind'),), attempt)
            if selected is not attempt:
                selected = dict(selected, latest_request_failure={
                    'record_id': attempt['record_id'], 'provider': attempt.get('provider', 'unknown'),
                    'kind': attempt['kind'], 'title': attempt['title'],
                    'retrieved_at': attempt['retrieved_at'], 'status': attempt.get('status'),
                    'error': attempt.get('error') or 'unexpected_http_status'})
        result[key] = selected
    return result
