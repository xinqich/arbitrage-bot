/* A small DOM contract harness: executes the shipped UI without a browser or network. */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
class Element{
  constructor(tag,attrs={}){this.tagName=tag.toUpperCase();this.attrs={...attrs};this.children=[];this.dataset={};this.listeners={};this._value=attrs.value;this._text='';this.hidden='hidden' in attrs;this.open='open' in attrs;this.disabled='disabled' in attrs;this.checked='checked' in attrs;this.className=attrs.class||'';this.name=attrs.name||'';this.type=attrs.type||'';this.id=attrs.id||'';this.href=attrs.href||'';this.classList={toggle:(name,on)=>{const classes=new Set(this.className.split(' ').filter(Boolean));if(on)classes.add(name);else classes.delete(name);this.className=[...classes].join(' ');}};}
  get hash(){return this.href.startsWith('#')?this.href:'';}
  get value(){if(this._value!==undefined)return this._value;if(this.tagName==='SELECT')return this.children.find(c=>c.tagName==='OPTION')?.value||'';return this.tagName==='OPTION'?this.textContent:'';}
  set value(v){this._value=String(v);}
  get childNodes(){return this.children;}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  set textContent(v){this._text=String(v);this.children=[];}
  append(...nodes){for(let n of nodes){if(typeof n==='string'){const t=new Element('#text');t.textContent=n;n=t;}n.parent=this;this.children.push(n);}}
  prepend(n){n.parent=this;this.children.unshift(n);}
  replaceChildren(...nodes){this.children=[];this._text='';this._value=this.tagName==='SELECT'?undefined:this._value;this.append(...nodes);}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  async fire(type){const event={target:this,preventDefault(){}};for(const f of this.listeners[type]||[])await f(event);}
  setAttribute(key,value){this.attrs[key]=value;if(key==='id')this.id=value;}
  matches(selector){const [base,attribute]=selector.split('[');if(base&&base.startsWith('.')){if(!this.className.split(' ').includes(base.slice(1)))return false;}else if(base&&base.toUpperCase()!==this.tagName)return false;if(attribute){const match=attribute.slice(0,-1).match(/^([^=]+)(?:=['"]?([^'"]+)['"]?)?$/);const key=match[1],expected=match[2];const value=key.startsWith('data-')?this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]:this[key]??this.attrs[key];if(value===undefined||value===false)return false;if(expected!==undefined&&String(value)!==expected)return false;}return true;}
  querySelectorAll(selector){if(selector.includes(' ')){const [parent,rest]=selector.split(/ (.*)/s);return this.querySelectorAll(parent).flatMap(e=>e.querySelectorAll(rest));}return this.children.flatMap(c=>[...(c.matches(selector)?[c]:[]),...c.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){return this.matches(selector)?this:this.parent?.closest(selector)||null;}
  scrollIntoView(){}
}
function build(row){const node=new Element(row.tag,row.attrs);node._text=row.text||'';for(const child of row.children||[])node.append(build(child));return node;}
const document=build(fixture.tree);document.createElement=tag=>new Element(tag);document.createTextNode=text=>{const n=new Element('#text');n.textContent=text;return n;};document.getElementById=id=>document.querySelectorAll('[id]').find(n=>n.id===id)||null;
const $=id=>{const value=document.getElementById(id);assert(value,'Missing element '+id);return value;};
const requests=[],saved=new Map();
const context=vm.createContext({document,location:{hash:'#overview'},window:{addEventListener(){}},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},setInterval(){},setTimeout,clearTimeout,AbortController,Intl,Date,console,crypto:require('crypto').webcrypto,
  FormData:class{constructor(form){this.values=form.querySelectorAll('input').concat(form.querySelectorAll('select')).filter(e=>e.name&&!(e.type==='checkbox'&&!e.checked)).map(e=>[e.name,e.type==='checkbox'?'on':e.value]);}[Symbol.iterator](){return this.values[Symbol.iterator]();}},
  fetch:async(path,options={})=>{requests.push([path,options.body]);let value;if(path==='/api/session')value={token:'test-session'};else if(path==='/api/status')value=fixture.state;else if(path.startsWith('/api/route-detail?')){const id=decodeURIComponent(path.split('=')[1]);value=fixture.details[id];assert(value,'Unknown detail '+id);}else if(path==='/api/action'){const body=JSON.parse(options.body);if(body.action.startsWith('preview_'))value=fixture.reviews[body.prediction_id];else value={status:'saved'};}else throw Error('Unexpected fetch '+path);return {ok:true,json:async()=>structuredClone(value)};}});
vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
const run=source=>vm.runInContext(source,context);
(async()=>{
  await new Promise(resolve=>setTimeout(resolve,40));
  assert.equal($('connection').textContent,'Connected locally');
  assert($('overview-dmarket').textContent.includes('$'));
  assert($('routes').hidden);assert(!$('overview').hidden);
  assert(!$('overview').textContent.includes('Evidence health'));
  run("location.hash='#search';navigate()");assert(!$('routes').hidden);assert(!document.getElementById('search'));
  run("setMode('paper')");assert($('route-list').textContent.includes('paper-ui'));assert(!$('route-list').textContent.includes('real-one'));
  assert.equal(run('reportForMode().mode'),'paper');assert.equal(saved.get('arbitrage-route-mode'),'paper');
  run("setMode('confirmed')");assert.equal(run('reportForMode().mode'),'confirmed');assert(!$('route-list').textContent.includes('paper-ui'));
  await run("selectRoute('real-one')");assert(!$('active-detail').hidden);assert(!$('receipt-panel').open);
  const form=$('real-receipt-form'),reference=form.querySelector('input[name=reference]');reference.value='keep my receipt';
  $('receipt-panel').open=true;
  const before=$('route-list').children[0];await run('refresh()');assert.equal(reference.value,'keep my receipt');assert($('receipt-panel').open);assert.equal($('route-list').children[0],before);
  const id=fixture.state.searches.confirmed.predictions[0].prediction_id;
  await run('reviewPaper('+JSON.stringify(id)+')');assert(!$('paper-preview').hidden);assert.equal($('paper-entry-button').textContent,'Start real trial');
  $('paper-route-name').value='keep trial name';const selected=$('paper-preview-content').children[0];await run('refresh()');assert.equal($('paper-route-name').value,'keep trial name');assert.equal($('paper-preview-content').children[0],selected);
  const headings=$('search-result').querySelectorAll('th').map(e=>e.textContent);assert.deepEqual(headings,['Rank','Sell scenario','Game A → B','Item A','Buy total A','Steam sale A','Steam net A','Item B','Buy total B','Sell total B','Sell net B','Profit','ROI']);
  run("setMode('paper')");assert($('paper-preview').hidden);assert($('active-detail').hidden);
  const unsupported=fixture.state.searches.paper.predictions.find(p=>p.engine_version!=='observed-depth-v2');
  if(unsupported){await run('reviewPaper('+JSON.stringify(unsupported.prediction_id)+')');assert($('paper-entry-button').disabled);assert($('paper-preview-content').textContent.includes('automatic paper'));}
  run("location.hash='#funds';navigate()");assert(!$('funds').hidden);assert($('funds-cards').textContent.includes('CSFloatNot connected'));
  const wallet=$('steam-wallet-form');wallet.querySelector('input[name=amount]').value='12.34';wallet.querySelector('input[name=reference]').value='Steam balance test';await wallet.fire('submit');await new Promise(resolve=>setTimeout(resolve,20));
  const body=requests.map(r=>r[1]&&JSON.parse(r[1])).filter(Boolean).find(r=>r.action==='steam_wallet');assert.equal(body.record.amount_cents,1234);
  assert(!requests.some(([path])=>/^https?:/.test(path)));
  console.log('UI DOM contracts passed: mode separation, navigation, 13 columns, details, paper eligibility, form/selection preservation and wallet submission.');
})().catch(error=>{console.error(error);process.exitCode=1;});
