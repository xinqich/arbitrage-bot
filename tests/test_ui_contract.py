from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_real_growth as fixtures
from arbitrage_v2.prediction import screen
from arbitrage_v2.paper_entry import enter, preview as paper_preview
from arbitrage_v2.real_routes import preview as real_preview
from arbitrage_v2.route_details import detail
from arbitrage_v2.web import overview
from arbitrage_v2.worker import Worker, search


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(); self.root={'tag':'document','attrs':{},'children':[]};self.stack=[self.root]
    def handle_starttag(self,tag,attrs):
        node={'tag':tag,'attrs':dict(attrs),'children':[]};self.stack[-1]['children'].append(node)
        if tag not in {'input','meta','link','br','hr','img'}:self.stack.append(node)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i]['tag']==tag:self.stack=self.stack[:i];break
    def handle_data(self,data):
        if data.strip():self.stack[-1]['children'].append({'tag':'#text','attrs':{},'text':data})


class UIContractTests(unittest.TestCase):
    def test_shipped_ui_modes_forms_details_and_refresh_contract(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node is required for the JavaScript UI contract check')
        ex=fixtures.RealGrowthTests();ex.setUp();self.addCleanup(ex.doCleanups)
        ex.funding();ex.open();ex.buy()
        p=next(p for p in screen(ex.journal,ex.watch,ex.policy,ex.at)['predictions'] if p['engine_version']=='observed-depth-v2' and p['quantity_a']==2)
        enter(ex.journal,{'route_id':'paper-ui','prediction_id':p['prediction_id']},ex.mandate,ex.policy,ex.at)
        for mode in ('paper','confirmed'):
            report=search(ex.journal,'grow',ex.watch,ex.policy,ex.mandate,ex.at,mode)
            ex.journal.append('search_report',dict(report,at=ex.at))
        worker=Worker(ex.journal,ex.watch,ex.policy,ex.mandate,ex.config,{})
        with patch('arbitrage_v2.web.now',return_value=ex.at):state=overview(worker)
        details={'real-one':detail(ex.journal,route_id='real-one',at=ex.at)};reviews={}
        for mode,report in state['searches'].items():
            for p in report['predictions']:
                identifier=p['prediction_id'];details[identifier]=detail(ex.journal,prediction_id=identifier)
                reviews[identifier]=(paper_preview if mode=='paper' else real_preview)(ex.journal,identifier,ex.mandate,ex.policy,ex.at)
        tree=Tree();tree.feed(Path('arbitrage_v2/static/index.html').read_text(encoding='utf-8-sig'))
        path=ex.journal.path.parent/'ui-contract.json';path.write_text(json.dumps({'tree':tree.root,'state':state,'details':details,'reviews':reviews}),encoding='utf-8')
        result=subprocess.run([node,'tests/check_ui.cjs',str(path),'arbitrage_v2/static/app.js'],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('UI DOM contracts passed',result.stdout)
