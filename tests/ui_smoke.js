const { JSDOM, VirtualConsole } = require('jsdom');
const crypto = require('crypto');
const BASE = 'http://127.0.0.1:8123';
function initData(id, name) {
  const d = { user: JSON.stringify({ id, first_name: name, username: 'u' + id }), auth_date: String(Math.floor(Date.now() / 1000)) };
  const chk = Object.keys(d).sort().map(k => `${k}=${d[k]}`).join('\n');
  const sec = crypto.createHmac('sha256', 'WebAppData').update('123456:TESTTOKEN').digest();
  d.hash = crypto.createHmac('sha256', sec).update(chk).digest('hex');
  return new URLSearchParams(d).toString();
}
const errors = []; let okc = 0, bad = 0;
const check = (n, c, x = '') => { if (c) { okc++; console.log('  ✓', n); } else { bad++; console.log('  ✗ FAIL', n, x); } };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  // подготовка: админ синхронизирует матчи
  const adm = initData(1, 'Админ');
  const r = await fetch(BASE + '/api/v2/admin/feed/sync', { method: 'POST', headers: { 'X-Telegram-Init-Data': adm, 'Content-Type': 'application/json' }, body: '{}' }).then(r => r.json());
  console.log('feed sync:', JSON.stringify(r).slice(0, 160));
  const vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.detail || e.message || e))); vc.on('error', (...a) => errors.push('console.error: ' + a.join(' '))); 
  const html = (await fetch(BASE + '/').then(r => r.text())).replace(/<script src="https:\/\/telegram[^>]*><\/script>/, '').replace(/<link[^>]*fonts[^>]*>/g, '');
  const dom = new JSDOM(html, { url: BASE + '/', runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE), o);
      w.Telegram = { WebApp: { initData: initData(777, 'Тест'), ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {}, HapticFeedback: { impactOccurred() {}, notificationOccurred() {} }, openLink() {}, openTelegramLink() {} } };
      const noop = new Proxy(function () {}, { get: (t, p) => (p === 'canvas' ? {} : noop), apply: () => noop, set: () => true });
      w.HTMLCanvasElement.prototype.getContext = () => noop;
      w.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ width: 300, height: 200 });
      w.scrollTo = () => {}; w.Element.prototype.scrollIntoView = () => {};
      w.WebSocket = require('ws');
    } });
  const w = dom.window, d = w.document, $ = id => d.getElementById(id), $$ = s => [...d.querySelectorAll(s)];
  w.addEventListener('error', e => errors.push('window.error: ' + e.message));
  w.addEventListener('unhandledrejection', e => errors.push('unhandled: ' + (e.reason && e.reason.message)));
  await sleep(2500);
  const M = w.MSP; check('MSP-мост и новые модули загружены', !!M && !!M.U && Object.keys(M.tabs).length > 10, Object.keys(M.tabs));
  check('баланс в шапке', $('balance').textContent.replace(/\s/g, '') === '1000', $('balance').textContent);
  check('чип уровня в шапке', !!$('lvl-chip'));
  check('матчи автозагрузки отрисованы', $$('#event-list .match').length > 5, $$('#event-list .match').length);
  check('лига и время старта в карточке', /· |Сегодня|Завтра/.test($('event-list').textContent) && $$('.bell').length > 0);
  const tab = async n => { await M.showTab(n); await sleep(250); };
  const act = async (a, attrs = {}) => { const el = d.createElement('button'); Object.entries(attrs).forEach(([k, v]) => (el.dataset[k] = v)); await M.actions[a](el, { target: el }); await sleep(200); };
  console.log('\n== ставка на матч и 🔔');
  const first = $$('#event-list [data-action="odd"]')[0]; first.click(); await sleep(100);
  check('открылся купон', !$('bet-slip-modal').classList.contains('hidden'));
  $('slip-submit-btn').click(); await sleep(1200); check('ставка принята, баланс 900', $('balance').textContent.replace(/\s/g, '') === '900' || +$('balance').textContent.replace(/\s/g, '') < 1000, $('balance').textContent);
  $$('.bell')[0].click(); await sleep(1200); check('подписка 🔔 включена', $$('.bell.on').length === 1);
  console.log('\n== разделы');
  for (const n of ['games', 'bonus', 'profile', 'ledger', 'achievements', 'referral', 'social', 'history', 'rating', 'admin']) { await tab(n); check('вкладка ' + n + ' открыта', !$(n).classList.contains('hidden') && $(n).textContent.length > 30); }
  check('профиль: уровень и меню', /Уровень 1/.test($('profile-body').textContent === '' ? '' : (await (async () => { await tab('profile'); return $('profile-body').textContent; })())));
  check('журнал содержит ставку', /Ставка/.test((await (async () => { await tab('ledger'); await sleep(600); return $('ledger-list').textContent; })())));
  check('админ: статус автозагрузки', /включена/.test((await (async () => { await tab('admin'); await sleep(800); return $('feed-status').textContent; })())), $('feed-status').textContent);
  await tab('bonus'); await sleep(600); $$('[data-action="daily-claim"]')[0].click(); await sleep(1500); check('ежедневный бонус получен', /Получено сегодня/.test($('bonus-body').textContent));
  check('3 турнира созданы (открытый виден)', /Недельный забег/.test($('bonus-body').textContent), $('bonus-body').textContent.slice(0, 200));
  console.log('\n== кейсы');
  await tab('cases'); await sleep(600); const cards = $$('.case-card'); check('6 кейсов с RTP', cards.length === 6 && /RTP 9\d/.test($('cases-body').textContent), cards.length);
  cards[0].click(); await sleep(200); check('шторка кейса: предметы и шансы', $$('#msp-sheet .it').length === 9 && /%/.test($('msp-sheet').textContent));
  $('c-open').click(); await sleep(5600); check('рулетка отыграла, дроп показан', !!$('c-result').querySelector('.drop-card'), $('c-result').innerHTML.slice(0, 80));
  w.MSP.actions['sheet-close'](); await M.actions['cases-tab']({ dataset: { t: 'inv' } }); await sleep(600);
  const inv = $$('#cases-body .it[data-action="inv-toggle"]'); check('инвентарь', inv.length >= 1, inv.length);
  inv[0].click(); await sleep(400); await M.actions['cases-tab']({ dataset: { t: 'up' } }); await sleep(800);
  const tg = $$('.target'); check('апгрейдер: цели с шансами', tg.length > 0 && /%/.test(tg[0].textContent), tg.length);
  if (tg.length) { tg[0].click(); await sleep(150); $('up-go').click(); await sleep(4200); check('апгрейд выполнен (результат показан)', /УСПЕХ|Неудача/.test($('up-res').textContent), $('up-res').textContent); }
  await M.actions['cases-tab']({ dataset: { t: 'coll' } }); await sleep(600); check('коллекции', $$('.coll').length === 6);
  console.log('\n== игры');
  await tab('crash'); await sleep(800); check('краш: раунд НЕ идёт сам, кнопка «Играть»', /^Играть$/.test($('cr-btn').textContent), $('cr-btn').textContent);
  $('cr-btn').click(); await sleep(1500); check('краш: после «Играть» пошёл раунд (есть хеш, кнопка «Забрать»)', /хеш seed/.test($('cr-fair').textContent) && /Забрать/.test($('cr-btn').textContent), $('cr-btn').textContent);
  $('cr-btn').click(); await sleep(1200); check('краш: забрал или крэш', /забрали|Крэш/i.test($('cr-status').textContent), $('cr-status').textContent);
  await tab('mines'); $('mn-mines').value = 3; $$('[data-action="mn-start"]')[0].click(); await sleep(1200); check('мины: партия началась (25 ячеек)', $$('.mcell').length === 25 && $('mn-setup').classList.contains('hidden'));
  $$('.mcell')[12].click(); await sleep(1200); check('мины: ячейка открыта или подрыв', $$('.mcell.safe').length === 1 || /БУМ/.test($('mn-res').textContent));
  if ($$('.mcell.safe').length) { $('mn-cashout').click(); await sleep(1200); check('мины: кэшаут', /\+\d/.test($('mn-res').textContent), $('mn-res').textContent); }
  await tab('plinko'); await sleep(300); check('plinko: таблица множителей', $$('#pl-mults span').length === 13); $$('[data-action="pl-drop"]')[0].click(); await sleep(4000); check('plinko: результат', /x[\d.]+/.test($('pl-res').textContent), $('pl-res').textContent);
  await tab('dice'); $$('[data-action="dc-go"]')[0].click(); await sleep(1800); check('dice: результат', $('dc-res').textContent.length > 0, $('dc-res').textContent);
  await tab('wheel'); $$('[data-action="wh-go"]')[0].click(); await sleep(5200); check('колесо: результат', $('wh-res').textContent.length > 0, $('wh-res').textContent);
  await tab('coin'); $$('[data-action="cn-go"]')[0].click(); await sleep(2300); check('монетка: результат', $('cn-res').textContent.length > 0, $('cn-res').textContent);
  console.log('\n== профиль игрока и баланс');
  await act('player', { uid: '777' }); await sleep(900); check('публичный профиль', /Последние ставки/.test($('player-body').textContent));
  await M.loadMe(); await tab('ledger'); await sleep(700); const kinds = $('ledger-list').textContent; check('журнал: игры, кейсы, бонус', /Кейс/.test(kinds) && /бонус/i.test(kinds), kinds.slice(0, 160));
  console.log('\nОшибки JS:', errors.length ? errors : 'нет');
  console.log(`ИТОГО UI: ${okc} ok, ${bad} fail, ошибок JS: ${errors.length}`);
  w.close(); process.exit(bad || errors.length ? 1 : 0);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
