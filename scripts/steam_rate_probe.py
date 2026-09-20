"""Bounded, anonymous Steam diagnostic. No prices or trades enter the live journal."""
import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import sys
import time
from urllib.request import BaseHandler, build_opener, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from arbitrage_v2.collection_lock import collection_lock
from arbitrage_v2.journal import Journal
from arbitrage_v2.steam_public import ListingRedirect, allowed_url, capture_public
from arbitrage_v2.worker import controls


class StopProbe(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def retry_seconds(value, at):
    if value is None:
        return None
    try:
        if value.strip().isdigit():
            return int(value.strip())
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            return None
        return max(0, (target - datetime.fromisoformat(at)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def safe_headers(headers):
    return {key.lower(): str(value)[:256] for key, value in headers.items()
            if key.lower() in {'date', 'age', 'retry-after', 'cache-control', 'x-cache', 'x-cache-hits'}
            or key.lower().startswith(('x-ratelimit-', 'ratelimit-'))}


class Probe:
    def __init__(self, journal, output, plan, titles, http_budget, guard,
                 monotonic=time.monotonic, sleeper=time.sleep):
        self.journal, self.output, self.guard = journal, Path(output), guard
        self.monotonic, self.sleeper = monotonic, sleeper
        self.deadline = monotonic() + 900
        self.current, self.last_capture, self.request_started = None, None, None
        self.http_budget = http_budget
        self.data = {'schema_version': 1, 'run_id': str(uuid4()), 'started_at': now(),
                     'plan': plan, 'titles': titles, 'http_budget': http_budget,
                     'scope': 'anonymous Steam CS2 market listing pages; current bot reader',
                     'http_requests': [], 'checks': [], 'stages': [], 'status': 'running',
                     'inference': 'A short run cannot establish an exact or permanent rate limit.'}

    def save(self):
        self.output.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding='utf-8')

    def check(self):
        if self.monotonic() >= self.deadline:
            raise StopProbe('duration_budget')
        self.guard()

    def wait_until(self, target):
        while self.monotonic() < target:
            self.check()
            self.sleeper(min(1, target - self.monotonic()))

    def before_http(self, request):
        self.check()
        if request.get_method() != 'GET' or not allowed_url(request.full_url):
            raise StopProbe('unexpected_request_surface')
        if len(self.data['http_requests']) >= self.http_budget:
            raise StopProbe('http_budget')
        row = {'number': len(self.data['http_requests']) + 1,
               'check': self.current['number'], 'title': self.current['title'],
               'interval_seconds': self.current['interval_seconds'], 'started_at': now(),
               'status': None, 'headers': {}, 'retry_after_seconds': None}
        self.request_started = self.monotonic()
        identifier = 'attempt:probe:' + self.data['run_id'] + ':' + str(row['number'])
        self.journal.append('request_attempt', {'provider': 'steam_public', 'kind': 'details',
            'app_id': 730, 'title': row['title'], 'started_at': row['started_at'],
            'diagnostic': True, 'probe_run_id': self.data['run_id'], 'http_request': row['number']}, identifier)
        self.data['http_requests'].append(row)
        self.save()
        return request

    def after_http(self, response):
        row = self.data['http_requests'][-1]
        headers = safe_headers(response.headers)
        row.update(status=response.code, headers=headers,
                   response_seconds=round(self.monotonic() - self.request_started, 3),
                   retry_after_seconds=retry_seconds(headers.get('retry-after'), now()))
        self.save()
        return response

    def append(self, category, payload, identifier=None):
        # capture_public's logical attempt is accounted at the HTTP transport hook
        # instead, including redirects. Never insert its diagnostic quote into live evidence.
        if category == 'capture':
            self.last_capture = payload
        return identifier or 'diagnostic:' + str(uuid4())

    def run(self, samples, fetch=capture_public, opener=None):
        next_start = self.monotonic()
        try:
            for interval in self.data['plan']:
                stage = {'interval_seconds': interval, 'planned_checks': samples, 'successful_checks': 0}
                self.data['stages'].append(stage)
                for _ in range(samples):
                    self.wait_until(next_start)
                    self.check()
                    number = len(self.data['checks']) + 1
                    title = self.data['titles'][(number - 1) % len(self.data['titles'])]
                    self.current = {'number': number, 'title': title, 'interval_seconds': interval,
                                    'started_at': now()}
                    started = self.monotonic()
                    first_http = len(self.data['http_requests'])
                    self.last_capture = None
                    result = fetch(self, 730, title, opener)
                    self.current.update(status=result.get('status'), error=result.get('error'),
                                        elapsed_seconds=round(self.monotonic()-started, 3),
                                        http_requests=len(self.data['http_requests'])-first_http)
                    if self.last_capture and self.last_capture.get('payload'):
                        self.current['book_observed_at'] = self.last_capture['payload']['result']['histogram']['date']
                    self.data['checks'].append(self.current)
                    if result.get('status') in {401, 403, 429}:
                        raise StopProbe('restriction_' + str(result['status']))
                    if result.get('status') != 200 or result.get('error'):
                        # A challenge or unfamiliar 200 page fails the same strict
                        # parser as the bot. Do not continue accelerating through it.
                        raise StopProbe('unusable_response')
                    stage['successful_checks'] += 1
                    self.save()
                    next_start = max(self.monotonic(), started + interval)
                print(json.dumps({'stage_seconds': interval, 'successful_checks': stage['successful_checks'],
                                  'http_requests': len(self.data['http_requests'])}), flush=True)
            self.data['status'] = 'completed_without_observed_restriction'
        except StopProbe as exc:
            self.data['status'] = str(exc)
        except KeyboardInterrupt:
            self.data['status'] = 'interrupted'
        finally:
            if self.data['status'] == 'running':
                self.data['status'] = 'unexpected_error'
            self.data['finished_at'] = now()
            completed = [s['interval_seconds'] for s in self.data['stages']
                         if s['successful_checks'] == s['planned_checks']]
            self.data['fastest_completed_interval_seconds'] = min(completed, default=None)
            self.data['http_request_count'] = len(self.data['http_requests'])
            self.save()
        return self.data


class HttpObserver(BaseHandler):
    handler_order = 400
    def __init__(self, probe):
        self.probe = probe
    def http_request(self, request):
        return self.probe.before_http(request)
    https_request = http_request
    def http_response(self, request, response):
        return self.probe.after_http(response)
    https_response = http_response


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Actually send the bounded probe; otherwise print its plan.')
    parser.add_argument('--intervals', default='30,15,10,5')
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--http-budget', type=int, default=80)
    args = parser.parse_args(argv)
    intervals = [int(v) for v in args.intervals.split(',')]
    if (not intervals or len(intervals)>6 or any(v<1 or v>60 for v in intervals)
            or intervals != sorted(set(intervals), reverse=True) or not 1<=args.samples<=10
            or not 1<=args.http_budget<=100):
        parser.error('Use 1–6 decreasing intervals of 1–60 seconds, 1–10 samples, and an HTTP budget of 1–100.')
    watch = json.loads((ROOT/'config/watchlist.json').read_text(encoding='utf-8-sig'))
    titles = [r['title'] for r in watch['items'] if r['app_id']==730
              and watch.get('item_sources',{}).get(r['title'],watch.get('steam_source'))=='steam_public']
    if not titles:
        parser.error('No configured direct-Steam items to test.')
    if not args.run:
        print(json.dumps({'intervals': intervals, 'checks_per_stage': args.samples,
                          'titles': titles, 'http_budget': args.http_budget, 'network_requests': 0}))
        return
    config = json.loads((ROOT/'config/local.json').read_text(encoding='utf-8-sig'))
    if config['host'] != '127.0.0.1':
        raise ValueError('Only the local desk is supported.')
    journal = Journal(ROOT/'data/research.sqlite3')
    def guard():
        if not controls(journal)['paused']:
            raise StopProbe('background_collection_not_paused')
    guard()
    with urlopen(f"http://127.0.0.1:{config['port']}/api/status",timeout=20) as response:
        status = json.load(response)
    if status['busy']:
        raise StopProbe('background_request_still_running')
    # Do not use this tool to ignore a saved Steam provider restriction.
    for key, value in status['health'].get('source_states',{}).items():
        if value.get('provider')=='steam_public' and value.get('scope','provider')!='item':
            raise StopProbe('existing_steam_source_block')
    used = sum(status['requests'].values())
    remaining = min(watch['total_request_allowance']-used,
                    watch.get('steam_public_request_allowance',1000)-status['requests'].get('steam_public',0))
    budget = min(args.http_budget, remaining)
    if budget <= 0:
        raise StopProbe('existing_request_allowance_exhausted')
    folder = ROOT/'data/steam-probes';folder.mkdir(parents=True,exist_ok=True)
    output = folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]+'.json')
    with collection_lock(folder/'probe.sqlite3'):
        probe = Probe(journal,output,intervals,titles,budget,guard)
        result = probe.run(args.samples,opener=build_opener(ListingRedirect(),HttpObserver(probe)))
    print(json.dumps({'report':str(output),'status':result['status'],
                      'http_requests':result['http_request_count'],
                      'fastest_completed_interval_seconds':result['fastest_completed_interval_seconds'],
                      'background_collection':'left paused'}),flush=True)


if __name__ == '__main__':
    main()
