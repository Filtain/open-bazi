'use strict';
const $ = id => document.getElementById(id);
let state = {token: '', providers: {}, connected: false, chart: null, report: null};
let dirty = false;
const el = (tag, text, cls) => {const n = document.createElement(tag); if(text !== undefined)n.textContent=text; if(cls)n.className=cls; return n;};
function status(id, text, error=false){$(id).textContent=text; $(id).className='status '+(error?'error':'success');}
async function api(path, body){
  const options=body === undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':state.token},body:JSON.stringify(body)};
  const r=await fetch('/api/'+path,options); const data=await r.json();
  if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'请求失败，请检查输入。'); return data;
}
function navigate(connection){$('workspace').hidden=connection; $('connection-page').hidden=!connection; for(const [id,on] of [['nav-chart',!connection],['nav-connect',connection]]){$(id).classList.toggle('active',on);$(id).setAttribute('aria-pressed',on);}window.scrollTo(0,0);}
$('nav-chart').onclick=()=>navigate(false); $('nav-connect').onclick=()=>navigate(true);
function updateConnection(model){$('connection-dot').textContent=state.connected?'已连接':'未连接';$('generate-report').textContent=state.connected?'生成 AI 解读':'连接模型后解读';if(model)status('connection-status','已连接 '+model+'。密钥仅在本次服务运行期间保留。');}
function providerChanged(){const p=state.providers[$('provider').value];$('base-url').value=p.base_url;$('base-url').readOnly=$('provider').value!=='custom';$('model').value=p.model;$('api-key').value='';$('key-link').hidden=!p.docs;if(p.docs)$('key-link').href=p.docs;}
$('provider').onchange=providerChanged;
$('calendar').onchange=()=>{$('leap-row').hidden=$('calendar').value!=='lunar';if($('leap-row').hidden)$('leap').checked=false;};
$('example').onclick=()=>{for(const [k,v] of Object.entries({year:1988,month:2,day:15,time:'23:30',gender:'1',calendar:'solar',clock:'historical',fold:''}))$(k).value=v;$('leap').checked=false;$('calendar').onchange();status('form-status','已填入公开测试示例，请点击生成命盘。');};
function birthInput(){return {calendar:$('calendar').value,year:+$('year').value,month:+$('month').value,day:+$('day').value,time:$('time').value,leap:$('leap').checked,clock:$('clock').value,gender:$('gender').value===''?null:+$('gender').value,fold:$('fold').value===''?null:+$('fold').value,selected_year:+$('selected-year').value};}
function readable(s){return s.replace('T',' ').replace('+08:00','');}
function renderChart(c){
  $('empty-state').hidden=true;$('chart-result').hidden=false;$('ai-panel').hidden=false;
  $('input-summary').textContent='标准时间 '+readable(c.standard_time)+' · 农历 '+c.lunar_date;
  $('warnings').replaceChildren(...c.warnings.map(w=>el('p',w,'notice')));
  $('pillars').replaceChildren(...c.pillars.map(p=>{const n=el('div',undefined,'pillar');n.append(el('div',p.label,'label'),el('div',p.ganzhi[0],'stem'),el('div',p.ganzhi[1],'branch'),el('div',p.ten_god,'god'),el('div',p.hidden.map(h=>h.stem+' '+h.ten_god).join(' · '),'hidden-stems'));return n;}));
  $('elements').replaceChildren(...Object.entries(c.elements).map(([name,count])=>{const n=el('div',undefined,'element');n.append(el('span',name),el('strong',count));return n;}));
  $('annual').replaceChildren(el('div',c.annual.year+' · '+c.annual.ganzhi+' · '+c.annual.ten_god,'annual-line'),el('p',readable(c.annual.start)+' 至 '+readable(c.annual.end)+'（结束时刻不包含）','field-note'));
  $('luck-list').replaceChildren();
  if(c.luck){const a=c.luck.nominal_age;$('luck-summary').textContent=c.luck.direction+' · 名义起运 '+a.years+'年'+a.months+'月'+a.days+'日'+a.hours+'小时 · 实际首运 '+readable(c.luck.start);$('luck-list').append(...c.luck.periods.map(p=>{const n=el('div',undefined,'luck-item');n.append(el('strong',p.ganzhi),el('p',readable(p.start)+' 起\n'+readable(p.end)+' 止（不含）'));return n;}));}
  else $('luck-summary').textContent='本次未提供传统排运参数，未计算大运。';
  $('calculation-details').replaceChildren(el('p','策略：'+c.profile+'；'+c.engine+'；时区数据：'+c.tzdata),...c.limitations.map(t=>el('p',t)));
}
$('birth-form').addEventListener('input',()=>{if(state.chart){dirty=true;status('form-status','资料已修改，请重新生成命盘后再解读。');}});
$('birth-form').onsubmit=async e=>{e.preventDefault();$('calculate').disabled=true;status('form-status','正在本地计算…');try{const c=await api('chart',birthInput());state.chart=c;state.report=null;dirty=false;renderChart(c);$('report-content').replaceChildren();status('report-status','');status('form-status','计算完成。请核对右侧标准时间与命盘。');}catch(err){dirty=true;status('form-status',err.message,true);}finally{$('calculate').disabled=false;}};
$('connection-form').onsubmit=async e=>{e.preventDefault();$('connect').disabled=true;status('connection-status','正在发送测试请求…');try{const r=await api('connect',{provider:$('provider').value,base_url:$('base-url').value,model:$('model').value,api_key:$('api-key').value});state.connected=true;$('api-key').value='';updateConnection(r.model);}catch(err){status('connection-status',err.message,true);}finally{$('connect').disabled=false;}};
$('disconnect').onclick=async()=>{try{await api('disconnect',{});state.connected=false;$('api-key').value='';updateConnection();status('connection-status','已清除连接。正在进行中的请求可能仍会完成并计费。');}catch(err){status('connection-status',err.message,true);}};
$('generate-report').onclick=async()=>{
  if(dirty){status('report-status','资料已修改，请先重新生成命盘。',true);return;}
  if(!state.connected){navigate(true);return;}if(!state.chart)return;
  $('generate-report').disabled=true;$('calculate').disabled=true;status('report-status','正在生成并检查引用，通常需要几十秒。请勿重复提交。');
  try{const input=state.chart.input;const r=await api('report',input);state.report=r;$('report-content').replaceChildren(el('p',r.notice,'notice'),...r.sections.map(s=>{const n=el('article',undefined,'report-section');const d=el('details');d.append(el('summary','查看计算事实与解释规则'));for(const id of s.fact_ids)d.append(el('p',r.facts.find(f=>f.id===id).text));for(const id of s.rule_ids){const rule=r.rules.find(x=>x.id===id);d.append(el('p',rule.title+'：'+rule.text+'（'+rule.source+'）'));}n.append(el('h3',s.title),el('p',s.text),d);return n;}));status('report-status','AI生成 · '+r.model+(r.usage.total_tokens?' · 本次用量 '+r.usage.total_tokens+' Token':''));}
  catch(err){status('report-status',err.message,true);}finally{$('generate-report').disabled=false;$('calculate').disabled=false;}
};
$('export').onclick=()=>{if(!state.chart)return;const data={application:'Open Bazi v0.1',notice:'含个人命盘信息，请谨慎保存与分享。AI内容已明确标识。',chart:state.chart,ai_generated_report:state.report};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='open-bazi-result.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('selected-year').value=new Date().getFullYear();
api('bootstrap').then(data=>{state={...state,...data};for(const [id,p] of Object.entries(data.providers)){const option=el('option',p.name);option.value=id;$('provider').append(option);}providerChanged();updateConnection(data.model);}).catch(err=>status('form-status','本地服务连接失败：'+err.message,true));
