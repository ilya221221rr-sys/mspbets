// UI-проверка первой волны MSP 3.0 (jsdom): бонус/страховка в купонах, фрибеты в трёх купонах,
// ставки на рынки в общей истории + карточка, «Поделиться» ссылкой и открытие ставки по ссылке,
// кнопка «Рынки матча» в ленте, сезонный пропуск, подписи журнала.
// Запуск (свежая база): LOCAL_DB=/tmp/ui/w1.db PLUS_BG=0 python tests/serve_local.py,
//   затем LOCAL_DB=/tmp/ui/w1.db python tests/ui_seed.py, затем NODE_PATH=/tmp/ui/node_modules node tests/wave1_ui.js
const { JSDOM, VirtualConsole } = require('jsdom');
const crypto = require('crypto');
const { execSync } = require('child_process');
const BASE = 'http://127.0.0.1:8123';
const DB = process.env.LOCAL_DB || '/tmp/ui/w1.db';
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

async function open(uid, name, startParam) {
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push(`[${uid}] jsdomError: ` + (e.detail || e.message || e)));
  vc.on('error', (...a) => errors.push(`[${uid}] console.error: ` + a.join(' ')));
  const html = (await fetch(BASE + '/').then((r) => r.text())).replace(/<script src="https:\/\/telegram[^>]*><\/script>/, '').replace(/<link[^>]*fonts[^>]*>/g, '');
  const shared = [];
  const dom = new JSDOM(html, {
    url: BASE + '/', runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE), o);
      w.Telegram = { WebApp: { initData: initData(uid, name), initDataUnsafe: startParam ? { start_param: startParam } : {}, ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {}, HapticFeedback: { impactOccurred() {}, notificationOccurred() {} }, openLink() {}, openTelegramLink(u) { shared.push(u); } } };
      const noop = new Proxy(function () {}, { get: (t, p) => (p === 'canvas' ? {} : noop), apply: () => noop, set: () => true });
      w.HTMLCanvasElement.prototype.getContext = () => noop;
      w.scrollTo = () => {}; w.Element.prototype.scrollIntoView = () => {};
      w.WebSocket = require('ws');
    },
  });
  const w = dom.window;
  w.addEventListener('error', (e) => errors.push(`[${uid}] window.error: ` + e.message));
  w.addEventListener('unhandledrejection', (e) => errors.push(`[${uid}] unhandled: ` + (e.reason && e.reason.message)));
  await sleep(3000);
  const d = w.document;
  return { w, d, M: w.MSP, shared, $: (id) => d.getElementById(id), $$: (s) => [...d.querySelectorAll(s)] };
}

(async () => {
  const me = asUser(777, 'Тест'), opp = asUser(888, 'Соперник');
  await me.get('/api/me'); await opp.get('/api/me');
  sql('UPDATE users SET balance=1000000 WHERE user_id IN (777, 888)');
  const A = await open(777, 'Тест');
  const { w, d, M, $, $$ } = A;
  const bal = () => num($('balance').textContent);
  const tab = async (n) => { await M.showTab(n); await sleep(500); };
  check('приложение загрузилось, мост MSP.plus есть', !!M && !!M.plus && $$('#event-list .match').length >= 6, $$('#event-list .match').length);
  await M.loadMe(); await sleep(300);

  console.log('\n== бонус и страховка в купонах');
  $$('[data-action="mode"][data-mode="express"]')[0].click(); await sleep(300);
  for (const id of [1, 3, 4]) { $$(`#event-list [data-action="odd"][data-id="${id}"][data-choice="1"]`)[0].click(); await sleep(200); }
  await sleep(300);
  let ex = ($('w1-ex-perk') || {}).textContent || '';
  check('обычный экспресс из 3 событий: бонус +5%', /Бонус экспресса\s*\+5%/.test(ex), ex);
  check('3 события — страховки ещё нет (подсказка «от 4»)', /Страховка — от 4/.test(ex), ex);
  $$('#event-list [data-action="odd"][data-id="6"][data-choice="1"]')[0].click(); await sleep(400);
  ex = $('w1-ex-perk').textContent;
  check('4 события с кф ≥ 1.30: +10% и «Застрахован»', /\+10%/.test(ex) && /Застрахован/.test(ex), ex);
  $('express-amount').value = '1000'; $('express-amount').dispatchEvent(new w.Event('input', { bubbles: true })); await sleep(200);
  const tot = M.state.cart.reduce((a, i) => a * i.odds, 1);
  const expBonus = Math.floor((Math.floor(1000 * Math.round(Math.round(tot * 100) / 100 * 100) / 100) - 1000) * 10 / 100);
  check(`бонус в PTS считается от суммы (≈ +${expBonus})`, num(($('w1-ex-perk').querySelector('.on b:nth-of-type(2)') || {}).textContent) === expBonus, $('w1-ex-perk').innerHTML.slice(0, 300));
  $$('[data-action="express-submit"]')[0].click(); await sleep(1500);
  const hist0 = await me.get('/api/history');
  check('экспресс принят', hist0.some((h) => h.kind === 'express' && h.legs.length === 4));
  $$('[data-action="mode"][data-mode="single"]')[0].click(); await sleep(300);

  console.log('\n== фрибеты в купонах');
  for (const a of [3000, 2000, 1500]) await me.post('/api/v2/freebets/admin/give', { user_id: 777, amount: a });
  await tab('bonus'); await sleep(800);
  check('в «Бонусах» баннер фрибетов (3)', /Фрибеты: 3/.test(($('w1-pass-bonus') || {}).textContent || ''), ($('w1-pass-bonus') || {}).textContent);
  await tab('matches');
  // 1) окно доп. рынков
  $$('#event-list .mk-btn[data-id="1"]')[0].click(); await sleep(1000);
  const pickMk = (re) => { const b = $$('#mk-body .mk-odd').find((x) => re.test(x.textContent)); if (b) b.click(); return b; };
  check('выбрали «Больше 2.5» в окне рынков', !!pickMk(/Больше 2\.5/)); await sleep(200);
  const fbMk = d.querySelector('#mk-slip [data-action="w1-fb-mk"]');
  check('в купоне рынка кнопка «🎁 Фрибетом 3»', fbMk && !fbMk.classList.contains('hidden') && /3/.test(fbMk.textContent), fbMk && fbMk.outerHTML);
  const b0 = bal();
  fbMk.click(); await sleep(300);
  check('открылся выбор фрибета (3 жетона, чистый выигрыш)', !$('w1-fb-modal').classList.contains('hidden') && $$('#w1-fb-pick .w1-fb-item').length === 3 && /Чистый выигрыш/.test($('w1-fb-pick').textContent));
  $$('#w1-fb-pick .w1-fb-item').find((x) => /3\s000/.test(x.textContent)).click(); await sleep(1500);
  const mb = await me.get('/api/v2/markets/my');
  check('ставка фрибетом 3 000 на тотал принята, баланс не списан', mb.bets.length === 1 && mb.bets[0].amount === 3000 && bal() >= b0, `${b0} → ${bal()} ${JSON.stringify(mb.bets[0] || {})}`);
  check('кнопка теперь «Фрибетом 2»', /2/.test(d.querySelector('#mk-slip [data-action="w1-fb-mk"] i').textContent));
  $$('[data-action="mk-close"]')[0].click(); await sleep(200);
  // 2) старый купон одинара
  $$('#event-list [data-action="odd"][data-id="4"][data-choice="2"]')[0].click(); await sleep(600);
  const fbSlip = d.querySelector('#bet-slip-modal [data-action="w1-fb-slip"]');
  check('в купоне П1/X/П2 кнопка «Поставить фрибетом»', fbSlip && !fbSlip.classList.contains('hidden'), fbSlip && fbSlip.outerHTML);
  fbSlip.click(); await sleep(300);
  $$('#w1-fb-pick .w1-fb-item').find((x) => /2\s000/.test(x.textContent)).click(); await sleep(1500);
  const h1 = await me.get('/api/history');
  check('одинар П2 фрибетом 2 000 принят, купон закрыт', h1.some((h) => h.kind === 'single' && h.points === 2000 && h.choice === '2') && $('bet-slip-modal').classList.contains('hidden'));
  // 3) экспресс+
  $$('#event-list .mk-btn[data-id="2"]')[0].click(); await sleep(1000);
  pickMk(/\(/); await sleep(150); $('mk-to-mx').click(); await sleep(250);
  $$('[data-action="mk-close"]')[0].click(); await sleep(200);
  $$('#event-list .mk-btn[data-id="5"]')[0].click(); await sleep(1000);
  pickMk(/Больше/); await sleep(150); $('mk-to-mx').click(); await sleep(250);
  $$('[data-action="mk-close"]')[0].click(); await sleep(300);
  const mxp = ($('w1-mx-perk') || {}).textContent || '';
  check('в купоне экспресса+ подсказка бонуса', /Ещё 1 событие|Бонус экспресса/.test(mxp), mxp);
  const fbMx = d.querySelector('#mx-panel [data-action="w1-fb-mx"]');
  check('в экспрессе+ кнопка «🎁 Фрибетом 1»', fbMx && !fbMx.classList.contains('hidden') && /1/.test(fbMx.textContent), fbMx && fbMx.outerHTML);
  fbMx.click(); await sleep(300);
  $$('#w1-fb-pick .w1-fb-item')[0].click(); await sleep(1500);
  const mx = await me.get('/api/v2/mexpress/my');
  check('экспресс+ фрибетом 1 500 принят, купон очищен', mx.bets.length === 1 && mx.bets[0].amount === 1500 && $('mx-panel').classList.contains('hidden'));
  check('фрибетов не осталось — кнопки скрыты', $$('.w1-fb-btn').every((b) => b.classList.contains('hidden')));

  console.log('\n== общая история ставок и карточка');
  await tab('history'); await sleep(1200);
  const rows = $$('#history-list .hist');
  const txt = rows.map((r) => r.textContent.replace(/\s+/g, ' '));
  check('в истории 4 ставки: экспресс, рынок, одинар, экспресс+', rows.length === 4, txt.join(' | ').slice(0, 400));
  check('новые сверху: экспресс+ → одинар → тотал → экспресс', /Экспресс\+/.test(txt[0]) && /Исход: П2/.test(txt[1]) && /Тотал/.test(txt[2]) && /Экспресс/.test(txt[3]), txt.map((t) => t.slice(0, 40)).join(' | '));
  check('у ставок фрибетом метка «🎁 Фрибет»', txt.filter((t) => /Фрибет/.test(t)).length === 3);
  check('у экспресса из 4 событий метки «🔥 +10%» и «🛡 Страховка»', /\+10%/.test(txt[3]) && /Страховка/.test(txt[3]), txt[3]);
  rows[2].click(); await sleep(300);
  check('карточка ставки на рынок открыта', !$('bet-modal').classList.contains('hidden') && /Арсенал — Челси/.test($('bet-card-title').textContent) && /Тотал/.test($('bet-card-choice').textContent), $('bet-card-title').textContent + ' / ' + $('bet-card-choice').textContent);
  check('в карточке: фрибет, без кнопки выкупа', /фрибет/.test($('bet-card-points').textContent) && /Ставка фрибетом/.test($('bet-card-legs').textContent) && $('bet-cashout-btn').classList.contains('hidden'));
  A.shared.length = 0;
  $$('#bet-modal [data-action="share"]')[0].click(); await sleep(800);
  const sh = decodeURIComponent(A.shared[0] || '');
  check('«Поделиться»: ссылка t.me/…?startapp=bet_m…', /startapp=bet_m\d+_[0-9a-f]{12}/.test(sh) && /Тотал/.test(sh), sh.slice(0, 300));
  $$('[data-action="bet-close"]')[0].click(); await sleep(200);
  rows[0].click(); await sleep(300);
  check('карточка экспресса+: 2 события с подписями рынков', $$('#bet-card-legs .leg').length === 2 && /Фора|Тотал/.test($('bet-card-legs').textContent));
  $$('[data-action="bet-close"]')[0].click(); await sleep(200);
  rows[3].click(); await sleep(300);
  check('старая карточка экспресса + блок «бонус и страховка»', /Зайдёт — бонус \+10%/.test($('bet-card-legs').textContent) && /Застрахован/.test($('bet-card-legs').textContent), $('bet-card-legs').textContent.slice(-200));
  A.shared.length = 0;
  $$('#bet-modal [data-action="share"]')[0].click(); await sleep(800);
  const shE = decodeURIComponent(A.shared[0] || '');
  check('старый экспресс тоже делится ссылкой bet_e…', /startapp=bet_e\d+_/.test(shE) && /Мой экспресс/.test(shE), shE.slice(0, 200));
  $$('[data-action="bet-close"]')[0].click(); await sleep(200);
  // выкуп старой ставки перерисовывает историю — новые рынки должны остаться
  await me.post('/api/predict', { event_id: 3, points: 500, choice: '1' });
  await tab('history'); await sleep(1200);
  check('после новой ставки история собрана заново (5 ставок)', $$('#history-list .hist').length === 5);

  console.log('\n== лента ставок: поставить на новые рынки');
  const ev2 = await opp.get('/api/v2/markets/event/2');
  const it = ev2.groups.find((g) => g.key === 'total').items[0];
  const ob = await opp.post('/api/v2/markets/bet', { event_id: 2, market: it.market, pick: it.pick, amount: 700, odds: it.odds });
  check('соперник поставил на тотал баскетбола', ob.status === 200);
  await tab('social'); await sleep(1000);
  const mkb = $$('#social-body .bl-mk[data-id="2"]')[0];
  check('в ленте у ставки кнопка «📊 все рынки матча»', !!mkb && /📊/.test(mkb.textContent));
  mkb.click(); await sleep(1000);
  check('по кнопке открылись все рынки этого матча', !$('mk-modal').classList.contains('hidden') && /Lakers/.test($('mk-body').textContent));
  $$('[data-action="mk-close"]')[0].click(); await sleep(200);
  check('в ленте фильтры: Все / Тоталы, форы, счёт / П1-X-П2 / Экспрессы / Можно скопировать', $$('#social-body .w1-chip').length === 5 && $$('#social-body .w1-chip.on')[0].dataset.f === 'all');
  const kinds = $$('#social-body .bl-kind').map((x) => x.textContent);
  check('«Все»: ставки всех игроков всех видов', ['Ординар', 'Экспресс ·', 'Рынок', 'Экспресс+'].every((k) => kinds.some((t) => t.includes(k))) && /Соперник/.test($('social-body').textContent) && /Тест/.test($('social-body').textContent), kinds.join(' | '));
  $$('#social-body [data-action="w1-feed-f"][data-f="markets"]')[0].click(); await sleep(900);
  const mk2 = $$('#social-body .bl-kind').map((x) => x.textContent);
  check('фильтр «Тоталы, форы, счёт»: только рынки и экспрессы+', mk2.length >= 3 && mk2.every((t) => /Рынок|Экспресс\+/.test(t)) && /Тотал/.test($('social-body').textContent), mk2.join(' | '));
  check('у своих ставок нет «Скопировать», у чужой — есть', $$('#social-body [data-action="bl-copy"]').length === 1);
  $$('#social-body [data-action="w1-feed-f"][data-f="single"]')[0].click(); await sleep(900);
  check('фильтр П1/X/П2: только ординары', $$('#social-body .bl-kind').every((x) => /Ординар/.test(x.textContent)) && $$('#social-body .bl-kind').length >= 2);
  $$('#social-body [data-action="w1-feed-f"][data-f="all"]')[0].click(); await sleep(600);

  console.log('\n== ставка по ссылке у друга');
  const shr = await opp.post('/api/v2/betcards/share', { kind: 'market', id: ob.body.id });
  const B = await open(999, 'Друг', shr.body.payload);
  await sleep(1500);
  check('у друга открылась ставка по ссылке', !B.$('w1-shared').classList.contains('hidden') && /Соперник/.test(B.$('w1-sh-body').textContent) && /Тотал/.test(B.$('w1-sh-body').textContent), B.$('w1-sh-body').textContent.slice(0, 200));
  const cpy = B.$$('[data-action="w1-sh-copy"]')[0];
  check('кнопка «Поставить так же»', !!cpy);
  cpy.click(); await sleep(400);
  B.$('dialog-fields').querySelector('input').value = '120';
  B.$('dialog-ok').click(); await sleep(1500);
  const fm = await asUser(999, 'Друг').get('/api/v2/markets/my');
  check('друг поставил так же: 120 на тот же тотал', fm.bets.length === 1 && fm.bets[0].amount === 120 && fm.bets[0].market === it.market, JSON.stringify(fm.bets[0] || {}));
  check('окно ставки закрылось', B.$('w1-shared').classList.contains('hidden'));
  // окно друга не закрываем: после close() таймеры приложения бросают ошибки jsdom

  console.log('\n== сезонный пропуск');
  await tab('bonus'); await sleep(800);
  check('баннер пропуска в «Бонусах»', /Сезонный пропуск/.test($('w1-pass-bonus').textContent));
  await tab('spass'); await sleep(800);
  check('пропуск: 30 уровней, обе дорожки', $$('#w1-pass-body .w1-lv').length === 30 && $$('#w1-pass-body .w1-rw.prem').length === 30);
  const lvl = num(d.querySelector('.w1-pass-lv b').textContent.split('/')[0]);
  check('уровень посчитан по ставкам (≥ 0)', lvl >= 0);
  check('цена уровней: 1-й — 50K, 2-й — 55K, 30-й — 195K', /^1\s*50K$/.test(d.querySelector('.w1-lv-n').textContent) && /55K/.test($$('.w1-lv-n')[1].textContent) && /195K/.test($$('.w1-lv-n')[29].textContent), $$('.w1-lv-n').slice(0, 2).map((x) => x.textContent).join(','));
  check('подпись: «Уровень 1 — 50 000 PTS ставок, каждый следующий дороже на 5 000»', /Уровень 1 — 50\s000 PTS ставок, каждый следующий дороже на 5\s000/.test($('w1-pass-body').textContent));
  sql("INSERT INTO transactions (user_id, amount, balance_after, kind, note, created_at) VALUES (777, -400000, 0, 'bet', 'тест', datetime('now') || '+00:00')");
  await new Promise((r) => setTimeout(r, 16000)); // кэш очков 15 с
  await tab('spass'); await sleep(800);
  const claim = $$('[data-action="w1-pass-claim"]')[0];
  check('после ставок — «Забрать награды»', !!claim, $('w1-pass-body').textContent.slice(0, 200));
  const b5 = bal();
  claim.click(); await sleep(1500);
  check('401 500 PTS ставок → уровень 6, награды забраны (6 клеток ✓)', bal() > b5 && $$('#w1-pass-body .w1-rw.free.got').length === 6 && /^6/.test(d.querySelector('.w1-pass-lv b').textContent), `${b5} → ${bal()} · ${d.querySelector('.w1-pass-lv b').textContent}`);
  $$('[data-action="w1-pass-buy"]')[0].click(); await sleep(300);
  $('dialog-ok').click(); await sleep(1500);
  check('премиум куплен', /Премиум активен/.test($('w1-pass-body').textContent));
  const claim2 = $$('[data-action="w1-pass-claim"]')[0];
  check('после покупки можно забрать премиум-награды', !!claim2);
  claim2.click(); await sleep(1500);
  check('премиум-награды получены', $$('#w1-pass-body .w1-rw.prem.got').length >= 5);
  await tab('profile');
  check('в меню профиля «Сезонный пропуск» и «Мои фрибеты»', !!d.querySelector('#profile-body [data-tab="spass"]') && !!d.querySelector('#profile-body [data-tab="freebets"]'));
  await tab('freebets'); await sleep(800);
  check('раздел фрибетов: фрибет из пропуска и история использованных', /Сезонный пропуск/.test($('w1-fb-body').textContent) && /использован/.test($('w1-fb-body').textContent), $('w1-fb-body').textContent.slice(0, 300));
  await tab('ledger'); await sleep(800);
  const lt = $('ledger-list').textContent;
  check('журнал: «Фрибет», «Сезонный пропуск», «Премиум-пропуск»', /Фрибет/.test(lt) && /Сезонный пропуск/.test(lt) && /Премиум-пропуск/.test(lt), lt.slice(0, 300));

  console.log('\n== админка: выдача фрибетов');
  await tab('admin'); await sleep(1200);
  check('в админке раздел «🎁 Фрибеты» с формой', !!$('w1-adm-fb') && !!$('w1-adm-fb-form') && /Фрибеты/.test($('w1-adm-fb').querySelector('summary').textContent));
  $('w1-adm-fb').open = true;
  $('w1-adm-fb-target').value = '@u777'; $('w1-adm-fb-amount').value = '4444'; $('w1-adm-fb-count').value = '2'; $('w1-adm-fb-days').value = '5'; $('w1-adm-fb-note').value = 'Тест админки';
  $('w1-adm-fb-form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await sleep(1500);
  const al = $('w1-adm-fb-list').textContent;
  check('выдано 2 × 4 444 по @username, видно в списке выданных', ($('w1-adm-fb-list').textContent.match(/4\s444/g) || []).length === 2 && /Тест админки/.test(al), al.slice(0, 200));
  const myFb = await me.get('/api/v2/freebets');
  check('у игрока появились 2 фрибета на 5 дней', myFb.active.filter((f) => f.amount === 4444 && f.note === 'Тест админки').length === 2);
  $$('#w1-adm-fb-list [data-action="w1-adm-fb-revoke"]')[0].click(); await sleep(300);
  $('dialog-ok').click(); await sleep(1200);
  check('«Отменить» в списке: один фрибет отменён', /отменён/.test($('w1-adm-fb-list').textContent) && (await me.get('/api/v2/freebets')).active.filter((f) => f.amount === 4444).length === 1);

  console.log('\n== фрибет на обычный экспресс');
  await tab('matches');
  $$('[data-action="mode"][data-mode="express"]')[0].click(); await sleep(300);
  for (const id of [3, 6]) { $$(`#event-list [data-action="odd"][data-id="${id}"][data-choice="2"]`)[0].click(); await sleep(200); }
  await sleep(300);
  const fbEx = d.querySelector('#express-body [data-action="w1-fb-ex"]');
  check('в купоне экспресса кнопка «🎁 Фрибетом»', fbEx && !fbEx.classList.contains('hidden'), fbEx && fbEx.outerHTML);
  const exBefore = (await me.get('/api/history')).filter((h) => h.kind === 'express').length;
  fbEx.click(); await sleep(400);
  check('окно выбора: «Экспресс из 2 событий»', !$('w1-fb-modal').classList.contains('hidden') && /Экспресс из 2 событий/.test($('w1-fb-pick').textContent));
  $$('#w1-fb-pick .w1-fb-item').find((x) => /4\s444/.test(x.textContent)).click(); await sleep(1600);
  const hx2 = (await me.get('/api/history')).filter((h) => h.kind === 'express');
  check('экспресс фрибетом 4 444 принят, купон очищен', hx2.length === exBefore + 1 && hx2[0].points === 4444 && (M.state.cart || []).length === 0, JSON.stringify(hx2[0] || {}).slice(0, 200));
  await tab('history'); await sleep(1200);
  const top = ($$('#history-list .hist')[0] || {}).textContent || '';
  check('в истории экспресс с меткой «🎁 Фрибет»', /Экспресс/.test(top) && /Фрибет/.test(top), top.replace(/\s+/g, ' ').slice(0, 120));

  console.log('\n== бонус за возвращение');
  const back = asUser(1001, 'Возвращенец');
  await back.get('/api/me');
  const ago5 = new Date(Date.now() - 5 * 864e5).toISOString();
  sql(`INSERT INTO transactions (user_id, amount, balance_after, kind, note, created_at) VALUES (1001, -30000, 0, 'bet', 'старая ставка', '${ago5}')`);
  const C = await open(1001, 'Возвращенец');
  await sleep(1800);
  check('при входе окно «С возвращением!»: 5 дней, +100 000 PTS', !C.$('w1-cb-modal').classList.contains('hidden') && /С возвращением/.test(C.$('w1-cb-body').textContent) && /5 дней/.test(C.$('w1-cb-body').textContent) && /\+100\s000/.test(C.$('w1-cb-body').textContent), C.$('w1-cb-modal') && C.$('w1-cb-body').textContent.slice(0, 160));
  C.$$('[data-action="w1-cb-later"]')[0].click(); await sleep(200);
  check('«Позже» закрывает окно', C.$('w1-cb-modal').classList.contains('hidden'));
  await C.M.showTab('bonus'); await sleep(1200);
  const cbb = C.$$('#w1-pass-bonus .w1-cb-banner')[0];
  check('в «Бонусах» баннер «Бонус за возвращение»', !!cbb && /Бонус за возвращение/.test(cbb.textContent) && /100\s000/.test(cbb.textContent));
  const cbal = num(C.$('balance').textContent);
  cbb.click(); await sleep(300);
  check('баннер снова открывает окно', !C.$('w1-cb-modal').classList.contains('hidden'));
  C.$$('[data-action="w1-cb-claim"]')[0].click(); await sleep(2200);
  check('«Забрать бонус»: +100 000 на баланс, окно закрыто', num(C.$('balance').textContent) >= cbal + 100000 && C.$('w1-cb-modal').classList.contains('hidden'), `${cbal} → ${C.$('balance').textContent}`);
  await C.M.showTab('bonus'); await sleep(1200);
  check('после получения баннера больше нет', !C.$$('#w1-pass-bonus .w1-cb-banner').length);
  check('повторно забрать нельзя (раз в неделю)', (await back.post('/api/v2/comeback/claim')).status === 400);
  await C.M.showTab('ledger'); await sleep(900);
  check('в журнале «👋 Бонус за возвращение»', /Бонус за возвращение/.test(C.$('ledger-list').textContent));
  check('у активного игрока окна возвращения нет', !$('w1-cb-modal') || $('w1-cb-modal').classList.contains('hidden'));

  console.log(errors.length ? '\nОшибки JS:\n' + errors.join('\n') : '\nОшибок JS нет');
  check('нет ошибок JS', errors.length === 0);
  console.log(`\nИтог: ${okc} ✓, ${bad} ✗`);
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
