import importlib.util
from pathlib import Path
import tempfile
import unittest
from io import BytesIO
from email.message import Message
from urllib.request import BaseHandler, Request, build_opener
from urllib.response import addinfourl

spec = importlib.util.spec_from_file_location('steam_rate_probe', Path(__file__).resolve().parents[1]/'scripts/steam_rate_probe.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class Journal:
    def __init__(self): self.rows = []
    def append(self, *args): self.rows.append(args)


class Clock:
    def __init__(self): self.at = 0
    def time(self): return self.at
    def sleep(self, seconds): self.at += seconds


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock, self.journal = Clock(), Journal()

    def probe(self, plan=None, budget=80, guard=lambda: None):
        return p.Probe(self.journal, Path(self.temp.name)/'report.json', plan or [30,15,10,5,2,1],
                       ['Fracture Case', 'Recoil Case'], budget, guard, self.clock.time, self.clock.sleep)

    def fetch(self, status=200, error=None):
        def call(probe, app, title, opener):
            probe.before_http(Request('https://steamcommunity.com/market/listings/730/Fracture%20Case'))
            probe.after_http(type('Response', (), {'code':status, 'headers':{'Retry-After':'60', 'Set-Cookie':'private'}})())
            probe.append('capture', {'payload':None})
            return {'status':status, 'error':error}
        return call

    def test_retry_after(self):
        at = '2026-09-19T12:00:00+00:00'
        self.assertEqual(p.retry_seconds('30',at),30)
        self.assertEqual(p.retry_seconds('Sat, 19 Sep 2026 12:02:00 GMT',at),120)
        self.assertEqual(p.retry_seconds('Sat, 19 Sep 2026 11:00:00 GMT',at),0)
        for value in [None,'','invalid','-1']:
            self.assertIsNone(p.retry_seconds(value,at))

    def test_only_safe_headers(self):
        self.assertEqual(p.safe_headers({'Retry-After':'60','Set-Cookie':'secret','Authorization':'secret','X-RateLimit-Remaining':'0'}),
                         {'retry-after':'60','x-ratelimit-remaining':'0'})

    def test_selected_ramp_and_no_price_writes(self):
        probe = self.probe()
        result = probe.run(5,self.fetch())
        self.assertEqual(result['status'],'completed_without_observed_restriction')
        self.assertEqual(result['http_request_count'],30)
        self.assertEqual(result['fastest_completed_interval_seconds'],1)
        self.assertEqual(self.clock.at,314)
        self.assertEqual({r[0] for r in self.journal.rows},{'request_attempt'})
        self.assertTrue(all(r[1]['diagnostic'] for r in self.journal.rows))

    def test_stop_first_bad_response(self):
        for status,error,expected in [(429,'http_429','restriction_429'),(403,'http_403','restriction_403'),
                                     (200,'invalid_or_unsupported_steam_page','unusable_response'),
                                     (503,'http_503','unusable_response')]:
            result = self.probe().run(5,self.fetch(status,error))
            self.assertEqual(result['status'],expected)
            self.assertEqual(len(result['checks']),1)
            self.assertIsNone(result['fastest_completed_interval_seconds'])
            self.assertEqual(result['http_requests'][0]['retry_after_seconds'],60)

    def test_budget_guard_and_deadline(self):
        result = self.probe(budget=2).run(5,self.fetch())
        self.assertEqual(result['status'],'http_budget')
        self.assertEqual(result['http_request_count'],2)
        def guard():
            if self.clock.at >= 61: raise p.StopProbe('operator_resumed')
        result = self.probe(guard=guard).run(5,self.fetch())
        self.assertEqual(result['status'],'operator_resumed')
        probe = self.probe()
        probe.deadline = self.clock.at + 1
        self.assertEqual(probe.run(5,self.fetch())['status'],'duration_budget')

    def test_redirects_count_actual_http_and_stop_on_429(self):
        class Transport(BaseHandler):
            handler_order = 100
            def __init__(self): self.calls = 0
            def https_open(self, request):
                self.calls += 1
                headers = Message()
                if self.calls == 1:
                    status = 302
                    headers['Location'] = 'https://steamcommunity.com/market/listings/730/Recoil%20Case'
                else:
                    status = 429
                    headers['Retry-After'] = '120'
                response = addinfourl(BytesIO(b''),headers,request.full_url,status)
                response.msg = 'test'
                return response
        probe, transport = self.probe(), Transport()
        opener = build_opener(p.ListingRedirect(),p.HttpObserver(probe),transport)
        result = probe.run(5,opener=opener)
        self.assertEqual(result['status'],'restriction_429')
        self.assertEqual([r['status'] for r in result['http_requests']],[302,429])
        self.assertEqual(result['checks'][0]['http_requests'],2)
        self.assertEqual(result['http_requests'][-1]['retry_after_seconds'],120)
        self.assertEqual(len(self.journal.rows),2)


if __name__ == '__main__': unittest.main()
