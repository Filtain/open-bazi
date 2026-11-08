'use strict';
const $ = id => document.getElementById(id);
let state = {token: '', providers: {}, connected: false, chart: null, report: null, chat: []};
let dirty = false, busy = false, reportGroup = 'summary';
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
function status(id, text, error = false) {
  $(id).textContent = text;
  $(id).className = 'status ' + (error ? 'error' : 'success');
}
async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Local-Token': state.token}, body: JSON.stringify(body)
  };
  const response = await fetch('/api/' + path, options);
  let data;
  try { data = await response.json(); } catch { throw new Error('本地服务未返回结果，请检查是否仍在运行。'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入。');
  return data;
}
function navigate(connection) {
  $('workspace').hidden = connection;
  $('connection-page').hidden = !connection;
  for (const [id, on] of [['nav-chart', !connection], ['nav-connect', connection]]) {
    $(id).classList.toggle('active', on);
    $(id).setAttribute('aria-pressed', String(on));
  }
  window.scrollTo(0, 0);
}
$('nav-chart').onclick = () => navigate(false);
$('nav-connect').onclick = () => navigate(true);
function setBusy(value) {
  busy = value;
  for (const id of ['calculate', 'generate-report', 'send-question', 'clear-chat', 'example', 'connect']) $(id).disabled = value;
  // Keep a snapshot stable throughout report/chat generation.
  for (const field of $('birth-form').querySelectorAll('input,select')) field.disabled = value;
  $('ai-panel').setAttribute('aria-busy', String(value));
}
function updateConnection(model) {
  $('connection-dot').textContent = state.connected ? '已连接' : '未连接';
  $('generate-report').textContent = !state.connected ? '连接模型后生成总报告' :
    state.report?.status === 'natal_ready' ? '继续生成大运流年' : state.report ? '重新生成总报告' : '生成总报告';
  $('calculate').textContent = state.connected ? '排盘并生成总报告 →' : '生成命盘 →';
  $('calculate-note').textContent = state.connected ? '排盘在本地完成；总报告将调用你的模型 API' : '本地计算 · 无需 API · 不自动保存';
  if (model) status('connection-status', '已连接 ' + model + '。返回排盘工作台，填写资料后即可生成总报告。');
}
function providerChanged() {
  const p = state.providers[$('provider').value];
  $('base-url').value = p.base_url;
  $('base-url').readOnly = $('provider').value !== 'custom';
  $('model').value = p.model;
  $('api-key').value = '';
  $('key-link').hidden = !p.docs;
  if (p.docs) $('key-link').href = p.docs;
}
$('provider').onchange = providerChanged;
$('calendar').onchange = () => {
  $('leap-row').hidden = $('calendar').value !== 'lunar';
  if ($('leap-row').hidden) $('leap').checked = false;
};
function markDirty() {
  if (state.chart) {
    dirty = true;
    status('form-status', '资料已修改。当前报告属于上一次命盘，请重新排盘后再解读或追问。');
  }
}
$('example').onclick = () => {
  for (const [k, v] of Object.entries({year: 1988, month: 2, day: 15, time: '23:30', gender: '1', calendar: 'solar', clock: 'historical', fold: ''})) $(k).value = v;
  $('leap').checked = false;
  $('calendar').onchange();
  markDirty();
  status('form-status', '已填入公开测试示例，请点击排盘。');
};
function birthInput() {
  return {calendar: $('calendar').value, year: +$('year').value, month: +$('month').value, day: +$('day').value,
    time: $('time').value, leap: $('leap').checked, clock: $('clock').value,
    gender: $('gender').value === '' ? null : +$('gender').value,
    fold: $('fold').value === '' ? null : +$('fold').value, selected_year: +$('selected-year').value};
}
function readable(s) { return s.replace('T', ' ').replace('+08:00', ''); }
function progress(stage) {
  const ids = ['step-chart', 'step-natal', 'step-outlook', 'step-chat'];
  ids.forEach((id, index) => {
    $(id).classList.toggle('done', index < stage);
    $(id).classList.toggle('current', index === stage);
    if (index === stage) $(id).setAttribute('aria-current', 'step');
    else $(id).removeAttribute('aria-current');
  });
}
function renderChart(c) {
  $('empty-state').hidden = true;
  for (const id of ['chart-result', 'rule-panel', 'ai-panel']) $(id).hidden = false;
  $('input-summary').textContent = '标准时间 ' + readable(c.standard_time) + ' · 农历 ' + c.lunar_date;
  $('warnings').replaceChildren(...c.warnings.map(w => el('p', w, 'notice')));
  $('pillars').replaceChildren(...c.pillars.map(p => {
    const n = el('div', undefined, 'pillar');
    n.append(el('div', p.label, 'label'), el('div', p.ganzhi[0], 'stem'), el('div', p.ganzhi[1], 'branch'),
      el('div', p.ten_god, 'god'), el('div', p.hidden.map(h => h.stem + ' ' + h.ten_god).join(' · '), 'hidden-stems'));
    return n;
  }));
  $('elements').replaceChildren(...Object.entries(c.elements).map(([name, count]) => {
    const n = el('div', undefined, 'element'); n.append(el('span', name), el('strong', count)); return n;
  }));
  $('annual').replaceChildren(el('div', c.annual.year + ' · ' + c.annual.ganzhi + ' · ' + c.annual.ten_god, 'annual-line'),
    el('p', readable(c.annual.start) + ' 至 ' + readable(c.annual.end) + '（结束时刻不包含）', 'field-note'));
  $('luck-list').replaceChildren();
  if (c.luck) {
    const a = c.luck.nominal_age;
    $('luck-summary').textContent = c.luck.direction + ' · 名义起运 ' + a.years + '年' + a.months + '月' + a.days + '日' + a.hours + '小时 · 实际首运 ' + readable(c.luck.start);
    $('luck-list').append(...c.luck.periods.map(p => {
      const n = el('div', undefined, 'luck-item');
      n.append(el('strong', p.ganzhi), el('p', readable(p.start) + ' 起\n' + readable(p.end) + ' 止（不含）')); return n;
    }));
  } else $('luck-summary').textContent = '未提供传统排运参数。补充后才能分析大运与流年的叠加。';
  $('calculation-details').replaceChildren(el('p', '策略：' + c.profile + '；' + c.engine + '；时区数据：' + c.tzdata), ...c.limitations.map(t => el('p', t)));
  const names = ['月令与日主', '通根在哪里', '生扶与泄耗克', '命格候选', '干支作用关系'];
  $('rule-content').replaceChildren(...c.analysis.observations.map((f, i) => {
    const d = el('details'); if (i === 0 || i === 3) d.open = true;
    d.append(el('summary', names[i]), el('p', f.text)); return d;
  }));
  progress(1);
}
function resetReading() {
  state.report = null; state.chat = [];
  $('report-content').replaceChildren(); $('chat-messages').replaceChildren();
  $('report-nav').hidden = true; $('chat-panel').hidden = true;
  $('question').value = '';
  status('report-status', ''); status('chat-status', '');
  updateConnection();
}
$('birth-form').addEventListener('input', markDirty);
$('birth-form').onsubmit = async e => {
  e.preventDefault(); if (busy) return;
  const input = birthInput(); setBusy(true); status('form-status', '正在本地排盘与整理规则依据…');
  let ok = false;
  try {
    const c = await api('chart', input);
    state.chart = c; dirty = false; resetReading(); renderChart(c); ok = true;
    status('form-status', '排盘完成。请核对标准时间、四柱和排运参数。');
  } catch (err) { dirty = true; status('form-status', err.message, true); }
  finally { setBusy(false); }
  if (ok && state.connected) await generateReport();
};
$('connection-form').onsubmit = async e => {
  e.preventDefault(); if (busy) return;
  setBusy(true); status('connection-status', '正在发送测试请求…');
  try {
    const r = await api('connect', {provider: $('provider').value, base_url: $('base-url').value, model: $('model').value, api_key: $('api-key').value});
    state.connected = true; $('api-key').value = ''; updateConnection(r.model);
  } catch (err) { status('connection-status', err.message, true); }
  finally { setBusy(false); }
};
$('disconnect').onclick = async () => {
  try {
    await api('disconnect', {}); state.connected = false; $('api-key').value = ''; updateConnection();
    status('connection-status', '已清除连接。已发送的请求可能仍计费，返回内容将不保存。');
  } catch (err) { status('connection-status', err.message, true); }
};
function evidenceDetails(reading, facts) {
  const d = el('details'); d.append(el('summary', '查看命盘依据与采用的规则'));
  for (const id of reading.fact_ids) {
    const f = facts.find(item => item.id === id);
    if (f) d.append(el('p', f.text));
  }
  for (const id of reading.rule_ids) {
    const r = state.report.rules.find(item => item.id === id);
    if (!r) continue;
    d.append(el('p', r.title + '：' + r.text), el('p', r.source, 'field-note'));
    if (r.url) { const a = el('a', '查阅参考文献 ↗', 'inline-link'); a.href = r.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; d.append(a); }
  }
  return d;
}
function readingBody(reading, facts) {
  const fragment = document.createDocumentFragment();
  fragment.append(el('p', reading.judgment || reading.answer, 'reading-judgment'));
  for (const [key, label] of [['basis', '为什么这样看'], ['conditions', '成立条件与不同可能'], ['advice', '可以怎样做']]) {
    const p = el('p'); p.append(el('strong', label + '：'), document.createTextNode(reading[key])); fragment.append(p);
  }
  fragment.append(evidenceDetails(reading, facts)); return fragment;
}
function groupOf(s) {
  if (s.id === 'summary') return 'summary';
  if (s.id.startsWith('luck_')) return 'luck';
  if (s.id.startsWith('year_')) return 'years';
  return 'natal';
}
function renderReport() {
  const r = state.report;
  $('report-nav').hidden = false;
  if (r.status !== 'complete') reportGroup = 'natal';
  for (const button of document.querySelectorAll('[data-report-group]')) {
    const active = button.dataset.reportGroup === reportGroup;
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    button.disabled = r.status !== 'complete' && button.dataset.reportGroup !== 'natal';
    if (button.dataset.reportGroup === 'luck') button.textContent = state.chart.luck ? '八步大运' : '大运待补全';
  }
  $('report-content').replaceChildren(el('p', r.notice, 'notice'), ...r.sections.filter(s => groupOf(s) === reportGroup).map(s => {
    const n = el('article', undefined, 'report-section');
    n.append(el('h3', s.title), readingBody(s, r.facts));
    const f = r.facts.find(f => f.id === s.id);
    if (s.id.startsWith('year_') && f?.data) {
      const intervals = el('div', undefined, 'year-segments');
      f.data.segments.forEach(segment => intervals.append(el('p', readable(segment.start) + ' 至 ' + readable(segment.end) + ' · ' + (segment.ganzhi ? segment.ganzhi + '大运' : segment.status), 'field-note')));
      n.insertBefore(intervals, n.children[1]);
    }
    return n;
  }));
  $('chat-panel').hidden = r.status !== 'complete';
  updateConnection();
}
for (const button of document.querySelectorAll('[data-report-group]')) button.onclick = () => { reportGroup = button.dataset.reportGroup; renderReport(); };
async function generateReport() {
  if (busy) return;
  if (dirty) { status('report-status', '资料已修改，请先重新排盘。', true); return; }
  if (!state.connected) { navigate(true); return; }
  if (!state.chart) return;
  setBusy(true);
  try {
    if (state.report?.status !== 'natal_ready') {
      progress(1); status('report-status', '第1步 / 2：正在分析命盘、旺衰、命格与喜忌。请勿重复提交。');
      const natal = await api('report', state.chart.input);
      state.report = natal; state.chat = []; $('chat-messages').replaceChildren();
      reportGroup = 'natal'; renderReport();
    }
    progress(2); status('report-status', '第2步 / 2：命盘分析已完成，正在合参大运与逐年趋势。可以先阅读下方命盘分析。');
    state.report = await api('report/' + state.report.id + '/outlook', {});
    reportGroup = 'summary'; renderReport(); progress(3);
    status('report-status', '总报告已完成 · ' + state.report.model + (state.report.usage.total_tokens ? ' · 共 ' + state.report.usage.total_tokens + ' Token' : '') + '。可切换章节，或在下方继续追问。');
  } catch (err) {
    status('report-status', err.message + (state.report?.status === 'natal_ready' ? ' 命盘分析已保留，点击“继续生成大运流年”即可续接。' : ''), true);
  } finally { setBusy(false); updateConnection(); }
}
$('generate-report').onclick = generateReport;
for (const button of document.querySelectorAll('[data-question]')) button.onclick = () => {
  $('question').value = button.dataset.question; $('question').focus();
};
$('chat-form').onsubmit = async e => {
  e.preventDefault(); if (busy) return;
  if (dirty) { status('chat-status', '出生资料已修改，请重新排盘后再提问。', true); return; }
  if (!state.connected) { navigate(true); return; }
  const question = $('question').value.trim(); if (!question || !state.report) return;
  setBusy(true); status('chat-status', '正在核对问题时间、原局依据和相应大运流年…');
  try {
    const r = await api('report/' + state.report.id + '/chat', {question});
    state.chat.push(r);
    const turn = el('article', undefined, 'chat-turn');
    turn.append(el('p', r.question, 'chat-question'));
    const reply = el('div', undefined, 'chat-answer');
    reply.append(el('p', '基于同一命盘 · ' + r.model, 'small-label'), readingBody(r.answer, r.facts));
    turn.append(reply); $('chat-messages').append(turn); $('question').value = '';
    status('chat-status', '已回答 · 本次附带流年 ' + r.scope_years.join('、') + ' · 对话 ' + state.chat.length + '/20 轮');
    turn.scrollIntoView({block: 'start', behavior: 'instant'});
  } catch (err) { status('chat-status', err.message, true); }
  finally { setBusy(false); }
};
$('clear-chat').onclick = async () => {
  if (busy || !state.report) return;
  try {
    await api('report/' + state.report.id + '/clear-chat', {});
    state.chat = []; $('chat-messages').replaceChildren(); status('chat-status', '对话已清空，命盘和报告保留。');
  } catch (err) { status('chat-status', err.message, true); }
};
$('export').onclick = () => {
  if (!state.chart) return;
  const data = {application: 'Open Bazi v0.2.0', notice: '含个人命盘信息和AI推演，请谨慎保存与分享。', chart: state.chart, ai_generated_report: state.report, conversation: state.chat};
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  const a = el('a'); a.href = url; a.download = 'open-bazi-result.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$('selected-year').value = Math.min(2099, new Date().getFullYear());
api('bootstrap').then(data => {
  state = {...state, ...data};
  for (const [id, p] of Object.entries(data.providers)) { const option = el('option', p.name); option.value = id; $('provider').append(option); }
  providerChanged(); updateConnection(data.model);
}).catch(err => status('form-status', '本地服务连接失败：' + err.message, true));
