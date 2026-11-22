// UI-проверка второй волны plus (jsdom): окно дуэли у создателя, экспресс+ с рынками,
// все виды ставок в профиле игрока и ленте, ежедневное колесо.
// Запуск: LOCAL_DB=/tmp/ui/p2.db PLUS_BG=0 python tests/serve_local.py  (+ посеять ESPN-матчи), затем
//         NODE_PATH=/tmp/ui/node_modules node tests/plus2_ui.js
const { JSDOM, VirtualConsole } = require('jsdom');
const crypto = require('crypto');
const { execSync } = require('child_process');
const BASE = 'http://127.0.0.1:8123';
const DB = process.env.LOCAL_DB || '/tmp/ui/p2.db';
const PY = process.env.PY || '/tmp/venv/bin/python';
function initData(id, name) {
  const d = { user: JSON.stringify({ id, first_name: name, username: 'u' + id }), auth_date: String(Math.floor(Date.now() / 1000)) };
  const chk = Object.keys(d).sort().map((k) => `${k}=${d[k]}`).join('\n');
  const sec = crypto.createHmac('sha256', 'WebAppData').update('123456:TESTTOKEN').digest();
  d.hash = crypto.createHmac('sha256', sec).update(chk).digest('hex');
  return new URLSearchParams(d).toString();
}
const asUser = (id, name) => {
  const h = { 'X-Telegram-Init-Data': initData(id, name), 'Content-Type': 'application/json' };
  return {
    get: (u) => fetch(BASE + u, { headers: h }).then((r) => r.json()),
    post: (u, b) => fetch(BASE + u, { method: 'POST', headers: h, body: JSON.stringify(b || {}) }).then(async (r) => ({ status: r.status, body: await r.json() })),
  };
};
const sql = (q) => execSync(`${PY} -c "import sqlite3,sys; c=sqlite3.connect('${DB}'); c.execute(sys.argv[1]); c.commit()" "${q}"`);
const errors = []; let okc = 0, bad = 0;
const check = (n, c, x = '') => { if (c) { okc++; console.log('  ✓', n); } else { bad++; console.log('  ✗ FAIL', n, x); } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const num = (t) => Number(String(t).replace(/[^\d]/g, ''));

(async () => {
  const opp = asUser(888, 'Соперник');
  await opp.get('/api/me');
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.detail || e.message || e)));
  vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));
  const html = (await fetch(BASE + '/').then((r) => r.text())).replace(/<script src="https:\/\/telegram[^>]*><\/script>/, '').replace(/<link[^>]*fonts[^>]*>/g, '');
  const dom = new JSDOM(html, {
    url: BASE + '/', runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE), o);
      w.Telegram = { WebApp: { initData: initData(777, 'Тест'), initDataUnsafe: {}, ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {}, HapticFeedback: { impactOccurred() {}, notificationOccurred() {} }, openLink() {}, openTelegramLink() {} } };
      const noop = new Proxy(function () {}, { get: (t, p) => (p === 'canvas' ? {} : noop), apply: () => noop, set: () => true });
      w.HTMLCanvasElement.prototype.getContext = () => noop;
      w.scrollTo = () => {}; w.Element.prototype.scrollIntoView = () => {};
      w.WebSocket = require('ws');
    },
  });
  const w = dom.window, d = w.document, $ = (id) => d.getElementById(id), $$ = (s) => [...d.querySelectorAll(s)];
  w.addEventListener('error', (e) => errors.push('window.error: ' + e.message));
  w.addEventListener('unhandledrejection', (e) => errors.push('unhandled: ' + (e.reason && e.reason.message)));
  await sleep(3000);
  const M = w.MSP;
  const bal = () => num($('balance').textContent);
  const tab = async (n) => { await M.showTab(n); await sleep(400); };
  check('приложение загрузилось', !!M && $$('#event-list .match').length >= 4, $$('#event-list .match').length);

  console.log('\n== экспресс+: рынки в экспрессе');
  const mkBtns = $$('#event-list .mk-btn');
  check('кнопка «Тоталы, форы» у 3 матчей ESPN (у киберспорта нет)', mkBtns.length === 3, mkBtns.length);
  const openMk = async (id) => { $$(`#event-list .mk-btn[data-id="${id}"]`)[0].click(); await sleep(900); };
  await openMk(1);
  check('в окне рынков есть «Исход матча»', /Исход матча/.test($('mk-body').textContent));
  const pick = (re) => { const b = $$('#mk-body .mk-odd').find((x) => re.test(x.textContent)); if (b) b.click(); return b; };
  check('выбрали «Больше 2.5»', !!pick(/Больше 2\.5/));
  await sleep(150);
  check('в купоне есть кнопка «＋ В экспресс»', /В экспресс/.test($('mk-to-mx').textContent));
  $('mk-to-mx').click(); await sleep(250);
  check('панель экспресса+ появилась: 1 событие', !$('mx-panel').classList.contains('hidden') && $('mx-count').textContent === '1');
  check('событие подписано с названием рынка', /Тотал: Больше 2\.5/.test($('mx-list').textContent), $('mx-list').textContent);
  check('кнопка в купоне сменилась на «В экспрессе — убрать»', /В экспрессе/.test($('mk-to-mx').textContent));
  check('предупреждение «добавь ещё событие», ставка недоступна', !$('mx-warn').classList.contains('hidden') && $('mx-submit').disabled);
  pick(/Обе забьют|Да/); await sleep(150); $('mk-to-mx').click(); await sleep(250);
  check('второе событие того же матча заменяет первое', $('mx-count').textContent === '1' && !/Больше 2\.5/.test($('mx-list').textContent), $('mx-list').textContent);
  $$('[data-action="mk-close"]')[0].click(); await sleep(200);
  check('на карточке матча видно, что он в экспрессе', /В экспрессе/.test($$('#event-list .mk-btn[data-id="1"]')[0].textContent));

  await openMk(2);
  check('баскетбол: есть фора', !!pick(/\(/)); await sleep(150);
  $('mk-to-mx').click(); await sleep(250);
  check('2 события, общий кф перемножается', $('mx-count').textContent === '2' && Number($('mx-total').textContent) > 2, $('mx-total').textContent);
  $$('[data-action="mk-close"]')[0].click(); await sleep(200);
  check('старая панель экспресса скрыта, пока есть экспресс+', d.body.classList.contains('mx-on'));

  // П1/X/П2 с карточки в режиме «Экспресс» тоже идут в экспресс+
  $$('[data-action="mode"][data-mode="express"]')[0].click(); await sleep(300);
  $$('#event-list [data-action="odd"][data-id="3"][data-choice="1"]')[0].click(); await sleep(300);
  check('П1 с карточки добавлен в экспресс+ (3 события)', $('mx-count').textContent === '3' && /Реал/.test($('mx-list').textContent), $('mx-list').textContent);
  check('кнопка П1 подсвечена', $$('#event-list [data-action="odd"][data-id="3"][data-choice="1"]')[0].classList.contains('selected'));
  check('старый купон пуст', (M.state.cart || []).length === 0);
  check('купон сохранён в localStorage', JSON.parse(w.localStorage.getItem('msp_mx')).length === 3);

  // коэффициент поменялся до ставки → 409 → купон обновляется
  sql('UPDATE events SET odds_1=2.1 WHERE id=3');
  const b0 = bal();
  $('mx-amount').value = '100'; $('mx-amount').dispatchEvent(new w.Event('input'));
  $('mx-submit').click(); await sleep(1500);
  const leg3 = [...$$('#mx-list .cart-item')].find((x) => /Реал/.test(x.textContent));
  check('после смены кф купон обновился до 2.10, ставка не прошла', leg3 && /2\.10/.test(leg3.textContent) && bal() === b0, leg3 && leg3.textContent);
  $('mx-submit').click(); await sleep(1500);
  const myMx = await asUser(777, 'Тест').get('/api/v2/mexpress/my');
  check('экспресс+ принят: 100 PTS, 3 события, кф с новым П1', myMx.bets.length === 1 && myMx.bets[0].amount === 100 && myMx.bets[0].legs.length === 3 && myMx.bets[0].legs.some((l) => l.market === '1x2' && l.odds === 2.1), JSON.stringify(myMx).slice(0, 300));
  const led = await asUser(777, 'Тест').get('/api/v2/ledger');
  check('в журнале списание −100 за экспресс+', led.some((x) => x.amount === -100 && x.kind === 'bet'), JSON.stringify(led).slice(0, 200));
  check('купон очищен', $('mx-panel').classList.contains('hidden') && JSON.parse(w.localStorage.getItem('msp_mx')).length === 0);
  check('старая панель экспресса снова доступна', !d.body.classList.contains('mx-on'));
  await tab('mk-bets'); await sleep(600);
  check('«Рынки и экспрессы+»: ставка с 3 событиями', /Экспресс\+ · 3/.test($('mk-bets-body').textContent) && /Тотал|Обе забьют/.test($('mk-bets-body').textContent), $('mk-bets-body').textContent.slice(0, 200));
  $$('[data-action="mkb-tab"][data-t="single"]')[0].click(); await sleep(600);
  check('вкладка одинаров на рынки работает', /Ставок на рынки пока нет|Ставок/.test($('mk-bets-body').textContent));
  await tab('profile');
  check('пункт меню «Рынки и экспрессы+» и «Ежедневное колесо»', !!d.querySelector('#profile-body [data-tab="mk-bets"]') && !!d.querySelector('#profile-body [data-tab="dwheel"]'));

  console.log('\n== чужая история ставок: все виды');
  const ev = await opp.get('/api/v2/markets/event/1');
  const tot = ev.groups.find((g) => g.key === 'total').items[0];
  const hcp = (await opp.get('/api/v2/markets/event/2')).groups.find((g) => g.key === 'hcp').items[0];
  const r1 = await opp.post('/api/v2/markets/bet', { event_id: 1, market: tot.market, pick: tot.pick, amount: 50, odds: tot.odds });
  const r2 = await opp.post('/api/v2/mexpress/bet', { amount: 40, legs: [{ event_id: 1, market: tot.market, pick: tot.pick, odds: tot.odds }, { event_id: 2, market: hcp.market, pick: hcp.pick, odds: hcp.odds }] });
  const r3 = await opp.post('/api/predict', { event_id: 3, points: 30, choice: '2' });
  check('соперник сделал ставки: рынок, экспресс+, одинар', r1.status === 200 && r2.status === 200 && r3.status === 200, JSON.stringify([r1, r2, r3]).slice(0, 300));
  const pb = d.createElement('button'); pb.dataset.uid = '888'; await M.actions.player(pb, { target: pb }); await sleep(1500);
  const pt = ($('bl-player') || {}).textContent || '';
  check('профиль игрока: видна ставка на тотал', /Тотал: /.test(pt), pt.slice(0, 300));
  check('профиль игрока: виден экспресс+ с форой', /Экспресс\+ · 2/.test(pt) && /Фора: /.test(pt), pt.slice(0, 300));
  check('профиль игрока: виден одинар П2', /Ординар/.test(pt));
  check('последняя ставка — первой (одинар)', /Ординар/.test(($$('#bl-player .bl-card')[0] || {}).textContent || ''));
  check('у чужих ставок есть «Скопировать»', $$('#bl-player [data-action="bl-copy"]').length === 3, $$('#bl-player [data-action="bl-copy"]').length);
  M.actions['player-close'] && M.actions['player-close']();
  await tab('social'); await sleep(800);
  const st = $('social-body').textContent;
  check('лента ставок: все три вида ставок соперника', /Тотал: /.test(st) && /Экспресс\+/.test(st) && /Ординар/.test(st) && /Соперник/.test(st), st.slice(0, 300));
  const b1 = bal();
  const cp = $$('#social-body [data-action="bl-copy"][data-kind="mx"]')[0];
  cp.click(); await sleep(400);
  check('диалог копирования открыт', !$('dialog').classList.contains('hidden'));
  const inp = $('dialog-fields').querySelector('input'); inp.value = '25';
  $('dialog-ok').click(); await sleep(1500);
  check('экспресс+ соперника скопирован (−25)', bal() <= b1 - 25 + 2000 && bal() !== b1, `${b1} → ${bal()}`);
  const mine = await asUser(777, 'Тест').get('/api/v2/mexpress/my');
  check('копия появилась в моих экспрессах+', mine.bets.length === 2 && mine.bets[0].amount === 25, JSON.stringify(mine).slice(0, 200));

  console.log('\n== ежедневное колесо');
  await tab('games');
  check('карточка колеса в «Играх» (доступно)', /Доступно бесплатное вращение/.test(($$('#games [data-tab="dwheel"]')[0] || {}).textContent || ''));
  await tab('bonus'); await sleep(500);
  check('баннер колеса в «Бонусах»', /Ежедневное колесо/.test(($('dw-bonus') || {}).textContent || ''));
  await sleep(800);
  check('«Бонусы» открылись целиком: ежедневный бонус и задания', /Ежедневный бонус/.test($('bonus-body').textContent) && /Задания дня/.test($('bonus-body').textContent));
  check('VIP-блока (рейкбек/кэшбэк) в «Бонусах» больше нет', !$('vb-body') && !/Рейкбек|Кэшбэк/.test($('bonus').textContent));
  await tab('dwheel'); await sleep(600);
  check('колесо: 10 секторов', $$('#dw-wheel path').length === 10);
  check('таблица призов от 20 000 до 500 000', /500\s000/.test($('dw-prizes').textContent) && /20\s000/.test($('dw-prizes').textContent), $('dw-prizes').textContent);
  const b2 = bal();
  $('dw-spin').click(); await sleep(300);
  check('во время вращения кнопка заблокирована', $('dw-spin').disabled);
  await sleep(5600);
  const won = num($('dw-res').textContent);
  check('приз показан (20 000 — 500 000)', won >= 20000 && won <= 500000, $('dw-res').textContent);
  await sleep(1500);
  check('баланс вырос на приз', bal() >= b2 + won, `${b2} + ${won} → ${bal()}`);
  check('кнопка скрыта, показан таймер до следующего', $('dw-spin').classList.contains('hidden') && /\d\d:\d\d:\d\d/.test($('dw-next').textContent), $('dw-next').textContent);
  const rot = Number(($('dw-wheel').style.transform.match(/-?[\d.]+/) || [0])[0]);
  const today = await asUser(777, 'Тест').get('/api/v2/dailywheel');
  check('колесо остановилось на выпавшем секторе', Math.abs((((rot + today.today.sector * 36) % 360) + 360) % 360) < 0.01, `${rot} / ${today.today.sector}`);
  const again = await asUser(777, 'Тест').post('/api/v2/dailywheel/spin');
  check('второй раз за сутки — нельзя', again.status === 400);
  await tab('ledger'); await sleep(800);
  check('в журнале: «Ежедневное колесо»', /Ежедневное колесо/.test($('ledger-list').textContent));

  console.log('\n== дуэль: создатель видит итог');
  await tab('duels'); await sleep(500);
  $('du-bet').value = '100';
  $$('[data-action="du-create"]')[0].click(); await sleep(1200);
  check('вызов создан', $$('#du-mine .du-card').length === 1);
  const code = $$('#du-mine .du-code')[0].dataset.code;
  const j = await opp.post('/api/v2/duels/join', { code });
  check('соперник принял вызов (API)', j.status === 200 && j.body.ok, JSON.stringify(j).slice(0, 200));
  await tab('games'); // создатель ушёл на другую вкладку — окно должно появиться всё равно
  await sleep(4200);
  check('у создателя открылось окно дуэли', $('du-modal') && !$('du-modal').classList.contains('hidden'));
  check('заголовок: «Соперник принял твой вызов!»', /Соперник.*принял твой вызов/.test($('du-head').textContent), $('du-head').textContent);
  await sleep(2000);
  const iWon = j.body.duel.winner_id === 777;
  check('итог для создателя совпадает с сервером', iWon ? /Победа/.test($('du-res-t').textContent) : /Поражение/.test($('du-res-t').textContent), $('du-res-t').textContent);
  $('du-ok').click(); await sleep(300);
  check('окно закрыто', $('du-modal').classList.contains('hidden'));
  await sleep(4000);
  check('повторно не показывается', $('du-modal').classList.contains('hidden'));
  await tab('duels'); await sleep(600);
  const hr = $$('#du-hist [data-action="du-detail"]')[0];
  check('дуэль в истории', !!hr);
  hr.click(); await sleep(300);
  check('из истории можно пересмотреть анимацию', !$('du-modal').classList.contains('hidden') && /Дуэль #/.test($('du-head').textContent));
  await sleep(2600);
  $$('#du-modal .du-fair-btn')[0].click(); await sleep(300);
  check('«Проверить честность» показывает seed', /seed: [0-9a-f]{16,}/.test($('dialog-text').textContent), $('dialog-text').textContent.slice(0, 120));

  console.log(errors.length ? '\nОшибки JS:\n' + errors.join('\n') : '\nОшибок JS нет');
  check('нет ошибок JS', errors.length === 0);
  console.log(`\nИтог: ${okc} ✓, ${bad} ✗`);
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
