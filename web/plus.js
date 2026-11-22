/* ==========================================================
   MSPBETS — новые разделы «как в настоящем казино и БК»:
     • 📊 доп. рынки на матчи (тоталы, форы, двойной шанс, обе забьют, точный счёт)
     • 🎡 европейская рулетка
     • ⚔️ дуэли 1 на 1
     • 💰 прогрессивный джекпот Mini / Major / Grand
     • ♻️ VIP-кэшбэк и рейкбек (раздел «Бонусы»)
   Подключается после остальных скриптов и использует мост window.MSP.
   Старые файлы не меняются: кнопки и блоки добавляются в готовую разметку.
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M || !M.U) return;
  const { api, post, toast, ok, fail, dialog, fmt, esc, haptic, sleep, state, U } = M;
  const $ = (id) => document.getElementById(id);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const main = document.querySelector('main.main');
  const anchor = $('history');
  const panel = (id, html) => { const s = document.createElement('section'); s.id = id; s.className = 'panel hidden'; s.innerHTML = html; main.insertBefore(s, anchor); return s; };
  const back = (to, label) => `<button class="back" type="button" data-tab="${to}">← ${label}</button>`;
  const head = (t, p) => `<div class="panel-head"><h2>${t}</h2>${p ? `<p>${p}</p>` : ''}</div>`;
  const navTo = (tab) => $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  const visible = (id) => { const el = $(id); return !!el && !el.classList.contains('hidden') && !document.hidden; };
  const short = (n) => { n = Number(n) || 0; const f = (v) => (Math.round(v * 10) / 10).toString().replace('.', ','); return n >= 1e6 ? f(n / 1e6) + 'M' : n >= 1e3 ? f(n / 1e3) + 'K' : String(n); };
  const dt = (iso) => { try { return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const debounce = (fn, ms) => { let t = 0; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const loading = '<div class="skeleton"></div><div class="skeleton"></div>';
  const tg = M.tg;

  // ==========================================================
  //  ИГРЫ: новые карточки в хабе + баннер джекпота
  // ==========================================================
  const hub = document.querySelector('#games .hub');
  if (hub) {
    hub.insertAdjacentHTML('afterbegin', `
      <button class="hub-card wide" data-tab="roulette" style="--hc:#2EE59D"><span class="pl-new">NEW</span><span class="hub-ico"><i class="pl-rl-ico"></i></span><span class="hub-name">Рулетка</span><span class="hub-sub">Европейская · одно зеро · число 35:1 · RTP 97,3%</span></button>
      <button class="hub-card wide" data-tab="duels" style="--hc:#FF5D7A"><span class="pl-new">NEW</span><span class="hub-ico">⚔️</span><span class="hub-name">Дуэли 1 на 1</span><span class="hub-sub">Монетка или кости против живого игрока · победитель забирает банк</span></button>`);
    hub.insertAdjacentHTML('beforebegin', `
      <div class="jp-banner">
        <div class="jp-top"><span class="jp-title">💰 Прогрессивный джекпот</span><button type="button" class="jp-info" data-action="jp-info">Как выиграть?</button></div>
        <div class="jp-grand"><small>GRAND</small><b class="jp-v" data-tier="grand">—</b></div>
        <div class="jp-row">
          <div class="jp-t jp-major"><small>MAJOR</small><b class="jp-v" data-tier="major">—</b></div>
          <div class="jp-t jp-mini"><small>MINI</small><b class="jp-v" data-tier="mini">—</b></div>
        </div>
        <div class="jp-recent" id="jp-recent"></div>
      </div>`);
  }

  // ==========================================================
  //  ДЖЕКПОТ
  // ==========================================================
  const JP = { shown: {}, data: null, raf: 0, busy: false, cfg: null };
  function jpAnimate(to) {
    const from = { ...JP.shown };
    const t0 = performance.now(), dur = 1400;
    cancelAnimationFrame(JP.raf);
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      Object.keys(to).forEach((key) => {
        const a = from[key] == null ? to[key] : from[key];
        JP.shown[key] = Math.round(a + (to[key] - a) * e);
      });
      $$('.jp-v').forEach((el) => { const v = JP.shown[el.dataset.tier]; if (v != null) el.textContent = fmt(v); });
      if (k < 1) JP.raf = requestAnimationFrame(step);
    };
    JP.raf = requestAnimationFrame(step);
  }
  async function jpLoad() {
    if (JP.busy) return;
    JP.busy = true;
    try {
      const d = await api('/api/v2/jackpot');
      JP.data = d;
      const to = {}; d.tiers.forEach((t) => { to[t.key] = t.amount; });
      jpAnimate(to);
      const rec = $('jp-recent');
      if (rec) rec.innerHTML = d.recent.length
        ? '<span class="jp-rlab">Сорвали:</span>' + d.recent.slice(0, 6).map((r) => `<span class="jp-chip t-${esc(r.tier)}">${esc(r.tier_name)} · ${esc(r.name)} · <b>${short(r.amount)}</b></span>`).join('')
        : '<span class="jp-rlab">Джекпот ещё никто не срывал — он может стать твоим</span>';
      if (d.unseen && d.unseen.length) jpWin(d.unseen);
    } catch (_) { /* тихо: баннер просто не обновится */ } finally { JP.busy = false; }
  }
  function jpWin(list) {
    let m = $('jp-win');
    if (!m) {
      m = document.createElement('div');
      m.id = 'jp-win'; m.className = 'modal modal-center hidden';
      m.innerHTML = '<div class="dialog jp-win-card"><div class="jp-rays"></div><div class="jp-coins">💰🪙💰🪙💰</div><div class="jp-win-t" id="jp-win-t"></div><div class="jp-win-a" id="jp-win-a"></div><div class="jp-win-s">Банк уже на твоём балансе</div><button class="btn btn-primary btn-block btn-lg" type="button" data-action="jp-win-ok">Забрать 🎉</button></div>';
      document.body.appendChild(m);
    }
    const total = list.reduce((s, w) => s + w.amount, 0);
    $('jp-win-t').textContent = 'ДЖЕКПОТ ' + list.map((w) => String(w.tier_name).toUpperCase()).join(' + ') + '!';
    $('jp-win-a').textContent = '+' + fmt(total) + ' PTS';
    m.classList.remove('hidden');
    haptic('success');
    M.loadMe().catch(() => {});
  }
  M.actions['jp-win-ok'] = async () => { $('jp-win').classList.add('hidden'); try { await post('/api/v2/jackpot/seen'); } catch (_) {} };
  M.actions['jp-info'] = () => {
    const d = JP.data; const t = d ? d.tiers : [];
    const line = (k) => { const x = t.find((y) => y.key === k); return x ? `${x.name}: старт ${fmt(x.seed)}, в банк идёт ${String(x.rate_pct).replace('.', ',')}% каждой ставки, срывается в среднем раз на ${short(x.every)} PTS ставок` : ''; };
    dialog({
      title: '💰 Как сорвать джекпот',
      text: `Любая ставка от ${fmt((d && d.min_bet) || 100)} PTS — на матчи, в играх, рулетке и кейсах — автоматически участвует в розыгрыше. Чем больше ставка, тем выше шанс.\n\n${line('grand')}.\n${line('major')}.\n${line('mini')}.\n\nОтчисления в банк — за счёт заведения, с тебя ничего лишнего не списывается. Выигрыш приходит через несколько секунд после ставки.`,
      okText: 'Понятно', cancelText: 'Закрыть',
    });
  };
  // Опрос: часто, пока открыты игры; редко — в фоне (чтобы показать окно победы)
  let jpLast = 0;
  setInterval(() => {
    const hot = visible('games') || visible('roulette');
    if (Date.now() - jpLast >= (hot ? 8000 : 45000) && !document.hidden) { jpLast = Date.now(); jpLoad(); }
  }, 2000);
  setTimeout(() => { jpLast = Date.now(); jpLoad(); }, 2500);
  M.tabs.games = ((orig) => async () => { if (orig) await orig(); jpLast = Date.now(); jpLoad(); })(M.tabs.games);

  // ==========================================================
  //  РУЛЕТКА
  // ==========================================================
  const WHEEL = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26];
  const RED = new Set([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]);
  const colorOf = (n) => (n === 0 ? 'rl-g' : RED.has(n) ? 'rl-r' : 'rl-b');
  const CHIPS = [10, 50, 100, 500, 1000, 5000, 25000];

  function wheelSvg() {
    const n = 37, a = 360 / n, R = 98, r = 64, rad = (d) => (d - 90) * Math.PI / 180;
    let s = '<defs><radialGradient id="rl-in" cx=".5" cy=".45" r=".6"><stop offset="0" stop-color="#3a2a14"/><stop offset="1" stop-color="#140d05"/></radialGradient></defs>';
    s += `<circle r="99.5" fill="#1a1206" stroke="#FFC93C" stroke-width="1.6"/>`;
    WHEEL.forEach((num, i) => {
      const a0 = rad(i * a - a / 2), a1 = rad(i * a + a / 2);
      const p = (rr, ang) => `${(rr * Math.cos(ang)).toFixed(3)} ${(rr * Math.sin(ang)).toFixed(3)}`;
      const fill = num === 0 ? '#12a150' : RED.has(num) ? '#d11f3d' : '#16171f';
      s += `<path d="M ${p(R, a0)} A ${R} ${R} 0 0 1 ${p(R, a1)} L ${p(r, a1)} A ${r} ${r} 0 0 0 ${p(r, a0)} Z" fill="${fill}" stroke="#c9a227" stroke-width=".35"/>`;
      s += `<text transform="rotate(${(i * a).toFixed(3)}) translate(0 -84)" text-anchor="middle" dominant-baseline="central" font-size="8.6" font-weight="800" fill="#fff" font-family="Manrope, system-ui, sans-serif">${num}</text>`;
    });
    s += `<circle r="${r}" fill="url(#rl-in)" stroke="#c9a227" stroke-width="1"/>`;
    for (let i = 0; i < 8; i++) s += `<line x1="0" y1="0" x2="0" y2="-46" stroke="#c9a227" stroke-width="2.2" stroke-linecap="round" transform="rotate(${i * 45})"/>`;
    s += '<circle r="12" fill="#FFC93C" stroke="#8a6a12" stroke-width="2"/>';
    return s;
  }
  function boardHtml() {
    let h = '<button type="button" class="rl-cell rl-g z" data-action="rl-bet" data-k="n:0">0<i></i></button>';
    for (let row = 0; row < 12; row++) for (let c = 1; c <= 3; c++) {
      const n = row * 3 + c;
      h += `<button type="button" class="rl-cell ${colorOf(n)}" data-action="rl-bet" data-k="n:${n}">${n}<i></i></button>`;
    }
    h += [1, 2, 3].map((c) => `<button type="button" class="rl-cell o" data-action="rl-bet" data-k="col:${c}">2:1<small>колонна</small><i></i></button>`).join('');
    h += [['1', '1–12'], ['2', '13–24'], ['3', '25–36']].map(([d, l]) => `<button type="button" class="rl-cell o" data-action="rl-bet" data-k="dozen:${d}">${l}<small>дюжина</small><i></i></button>`).join('');
    h += [['low', '1–18'], ['even', 'Чёт'], ['red', '<span class="rl-dot rl-r"></span>'], ['black', '<span class="rl-dot rl-b"></span>'], ['odd', 'Нечет'], ['high', '19–36']]
      .map(([k, l]) => `<button type="button" class="rl-cell o" data-action="rl-bet" data-k="${k}">${l}<i></i></button>`).join('');
    return h;
  }
  panel('roulette', back('games', 'Игры') + head('Рулетка', 'Европейская · одно зеро · результат определяет сервер') + `
    <div class="jp-strip" data-tab="games">💰 Grand <b class="jp-v" data-tier="grand">—</b> · Major <b class="jp-v" data-tier="major">—</b> · Mini <b class="jp-v" data-tier="mini">—</b></div>
    <div class="rl-hist" id="rl-hist"></div>
    <div class="rl-wheel-box"><div class="rl-pin"></div><svg id="rl-wheel" viewBox="-100 -100 200 200" aria-hidden="true">${wheelSvg()}</svg><div class="rl-ball" id="rl-ball"></div></div>
    <div class="res-pop" id="rl-res">Расставь фишки и крути</div>
    <div class="gcard">
      <div class="label">Фишка</div>
      <div class="rl-chips" id="rl-chips">${CHIPS.map((c, i) => `<button type="button" class="rl-chip c${i}${c === 100 ? ' active' : ''}" data-action="rl-chip" data-v="${c}">${short(c)}</button>`).join('')}<button type="button" class="rl-chip erase" data-action="rl-chip" data-v="-1" title="Убирать фишки">✕</button></div>
      <div class="rl-board" id="rl-board">${boardHtml()}</div>
      <div class="rl-sum"><span>На столе: <b id="rl-total">0</b> PTS</span><span id="rl-spots">0 поз.</span></div>
      <div class="btn-row"><button class="btn btn-ghost" type="button" data-action="rl-clear">Очистить</button><button class="btn btn-ghost" type="button" data-action="rl-half">½</button><button class="btn btn-ghost" type="button" data-action="rl-double">×2</button></div>
      <button class="btn btn-primary btn-block btn-lg" type="button" style="margin-top:10px" id="rl-spin" data-action="rl-spin">Крутить</button>
      <div class="rtp-note">Число — 35:1 · дюжина и колонна — 2:1 · цвет, чёт/нечет, половина — 1:1. На зеро внешние ставки проигрывают. RTP 97,3% на любую ставку.</div>
    </div>`);

  const RL = { chip: 100, bets: {}, rot: 0, spinning: false };
  const rlTotal = () => Object.values(RL.bets).reduce((s, v) => s + v, 0);
  function rlRender() {
    $$('#rl-board .rl-cell').forEach((c) => {
      const v = RL.bets[c.dataset.k] || 0;
      const i = c.querySelector('i');
      i.textContent = v ? short(v) : '';
      c.classList.toggle('has', !!v);
    });
    $('rl-total').textContent = fmt(rlTotal());
    $('rl-spots').textContent = `${Object.keys(RL.bets).length} поз.`;
    $('rl-spin').textContent = rlTotal() ? `Крутить · ${fmt(rlTotal())} PTS` : 'Крутить';
  }
  async function rlHist() {
    try {
      const h = await api('/api/v2/roulette/history');
      $('rl-hist').innerHTML = h.length ? h.map((x) => `<span class="${colorOf(x.n)}">${x.n}</span>`).join('') : '<small class="muted-note" style="margin:0">Здесь появятся выпавшие числа</small>';
    } catch (_) {}
  }
  M.tabs.roulette = async () => { navTo('games'); rlRender(); rlHist(); };
  M.actions['rl-chip'] = (el) => { RL.chip = Number(el.dataset.v); $$('#rl-chips .rl-chip').forEach((b) => b.classList.toggle('active', b === el)); haptic('light'); };
  M.actions['rl-bet'] = (el) => {
    if (RL.spinning) return;
    const k = el.dataset.k;
    if (RL.chip < 0) { delete RL.bets[k]; }
    else {
      if (Object.keys(RL.bets).length >= 60 && !RL.bets[k]) return toast('Максимум 60 позиций', 'err');
      RL.bets[k] = (RL.bets[k] || 0) + RL.chip;
    }
    haptic('light'); rlRender();
  };
  M.actions['rl-clear'] = () => { if (!RL.spinning) { RL.bets = {}; rlRender(); } };
  M.actions['rl-half'] = () => { if (RL.spinning) return; Object.keys(RL.bets).forEach((k) => { RL.bets[k] = Math.floor(RL.bets[k] / 2); if (!RL.bets[k]) delete RL.bets[k]; }); rlRender(); };
  M.actions['rl-double'] = () => { if (RL.spinning) return; Object.keys(RL.bets).forEach((k) => { RL.bets[k] *= 2; }); rlRender(); };
  M.actions['rl-spin'] = async () => {
    if (RL.spinning) return;
    const total = rlTotal();
    if (!total) return toast('Поставь фишку на стол', 'err');
    if (state.me && total > state.me.balance) return toast('Мало баллов', 'err');
    RL.spinning = true;
    const btn = $('rl-spin'); btn.disabled = true; btn.textContent = 'Крутим…';
    $('rl-ball').classList.remove('on');
    $$('#rl-board .rl-cell.hit').forEach((c) => c.classList.remove('hit'));
    $('rl-res').textContent = 'Ставки сделаны, ставок больше нет…';
    try {
      const res = await post('/api/v2/roulette/spin', { bets: Object.entries(RL.bets).map(([key, amount]) => ({ key, amount })) });
      U.setBal(state.me ? state.me.balance - total : null);
      const idx = WHEEL.indexOf(res.number), a = 360 / 37;
      const target = ((-idx * a) % 360 + 360) % 360;
      const cur = ((RL.rot % 360) + 360) % 360;
      RL.rot += 360 * 6 + ((target - cur + 360) % 360);
      const w = $('rl-wheel');
      w.style.transform = `rotate(${RL.rot}deg)`;
      haptic('medium');
      await sleep(document.documentElement.classList.contains('lite') ? 1200 : 4700);
      $('rl-ball').classList.add('on');
      const cell = document.querySelector(`#rl-board .rl-cell[data-k="n:${res.number}"]`);
      if (cell) cell.classList.add('hit');
      const name = res.number === 0 ? 'зеро' : RED.has(res.number) ? 'красное' : 'чёрное';
      const el = $('rl-res');
      if (res.payout > 0) { el.innerHTML = `<span class="rl-num ${colorOf(res.number)}">${res.number}</span> ${name} · <span class="win">+${fmt(res.payout)} PTS</span>`; haptic('success'); }
      else { el.innerHTML = `<span class="rl-num ${colorOf(res.number)}">${res.number}</span> ${name} · не повезло`; haptic('error'); }
      U.after(res);
      rlHist();
    } catch (e) { fail(e); $('rl-res').textContent = 'Расставь фишки и крути'; M.loadMe().catch(() => {}); }
    finally { RL.spinning = false; btn.disabled = false; rlRender(); }
  };

  // ==========================================================
  //  ДУЭЛИ
  // ==========================================================
  const SIDE = { heads: '🦅 Орёл', tails: '👑 Решка' };
  const DIE = ['', '⚀', '⚁', '⚂', '⚃', '⚄', '⚅'];
  panel('duels', back('games', 'Игры') + head('Дуэли 1 на 1', 'Брось вызов игрокам — победитель забирает банк') + `
    <div class="gcard">
      <div class="label">Игра</div>
      <div class="seg3" id="du-game"><button class="seg-btn active" type="button" data-action="du-game" data-v="coin">🪙 Монетка</button><button class="seg-btn" type="button" data-action="du-game" data-v="dice">🎲 Кости</button></div>
      <div id="du-side-wrap"><div class="label" style="margin-top:10px">Твоя сторона</div>
        <div class="seg3" id="du-side"><button class="seg-btn active" type="button" data-action="du-side" data-v="heads">${SIDE.heads}</button><button class="seg-btn" type="button" data-action="du-side" data-v="tails">${SIDE.tails}</button></div></div>
      <div id="du-dice-note" class="muted-note hidden" style="margin:10px 0 0">У каждого по два кубика — у кого сумма больше, тот победил. Ничья — переброс.</div>
      <div style="margin-top:10px">${U.betBox('du-bet', 1000)}</div>
      <div class="toggle"><span>🔒 Приватная — только по коду для друга</span><button class="sw" type="button" id="du-private" data-action="du-private" aria-label="переключить"></button></div>
      <div class="du-prize">Победитель получит <b id="du-prize">1 900</b> PTS <small id="du-fee">· комиссия 5%</small></div>
      <button class="btn btn-primary btn-block btn-lg" type="button" data-action="du-create">⚔️ Бросить вызов</button>
    </div>
    <div class="gcard"><div class="label">Есть код от друга?</div><div class="amount-row"><input id="du-code" class="input" placeholder="Например K7M2QX" maxlength="8" autocomplete="off" style="text-transform:uppercase"><button class="btn btn-ghost" type="button" data-action="du-find">Найти</button></div></div>
    <div id="du-mine-wrap" class="hidden"><div class="sub-head">Мои вызовы</div><div id="du-mine"></div></div>
    <div class="sub-head">Открытые вызовы <small class="du-count" id="du-count"></small></div><div id="du-open">${loading}</div>
    <div id="du-live-wrap" class="hidden"><div class="sub-head">Только что сыграли</div><div id="du-live" class="du-live"></div></div>
    <div id="du-hist-wrap" class="hidden"><div class="sub-head">Мои дуэли <small class="du-count" id="du-stats"></small></div><div id="du-hist"></div></div>
    <div class="muted-note" style="margin-top:12px">Честная игра: при создании сервер фиксирует секретный seed и показывает его SHA-256. Результат = HMAC-SHA256(seed, «id дуэли:id соперника»). После дуэли seed раскрывается — нажми на дуэль в истории, чтобы проверить.</div>`);

  const DU = { game: 'coin', side: 'heads', priv: false, cfg: null, data: null, mineOpen: new Set(), timer: 0, loading: false };
  const duPrize = (a) => Math.floor(2 * a * (1 - ((DU.cfg && DU.cfg.fee) || 0.05)));
  function duUpdatePrize() { const v = Math.floor(Number($('du-bet').value)) || 0; $('du-prize').textContent = fmt(duPrize(v)); }
  $('du-bet').addEventListener('input', duUpdatePrize);
  const duWho = (d) => `${U.ava(d.creator_name, '', 'sm')}<div class="du-who"><b>${esc(d.creator_name)}</b><small>${d.game === 'coin' ? `🪙 Монетка · у него ${SIDE[d.pick] || ''}` : '🎲 Кости'}</small></div>`;
  function duCard(d) {
    return `<div class="du-card">${duWho(d)}<div class="du-amt"><b>${fmt(d.amount)}</b><small>банк ${fmt(d.prize)}</small></div>
      <button class="btn btn-sm btn-primary" type="button" data-action="du-join" data-code="${esc(d.code)}" data-id="${d.id}">Принять</button></div>`;
  }
  function duMineCard(d) {
    const left = d.expires_at ? Math.max(0, new Date(d.expires_at) - Date.now()) : 0;
    const hh = Math.floor(left / 36e5), mm = Math.floor(left % 36e5 / 6e4);
    return `<div class="du-card mine"><div class="du-who"><b>${d.game === 'coin' ? '🪙 Монетка · ' + (SIDE[d.pick] || '') : '🎲 Кости'}${d.private ? ' · 🔒' : ''}</b>
      <small>Код <b class="du-code" data-action="du-copy" data-code="${esc(d.code)}">${esc(d.code)}</b> · ждём соперника · ещё ${hh} ч ${mm} мин</small></div>
      <div class="du-amt"><b>${fmt(d.amount)}</b><small>банк ${fmt(d.prize)}</small></div>
      <div class="du-btns"><button class="btn btn-sm btn-ghost" type="button" data-action="du-share" data-code="${esc(d.code)}" data-amount="${d.amount}" data-game="${d.game}">📤</button>
      <button class="btn btn-sm btn-ghost" type="button" data-action="du-cancel" data-id="${d.id}">✕</button></div></div>`;
  }
  function duResultText(d, me) {
    if (d.status === 'cancelled') return ['↩️', 'Отменён', 'muted'];
    if (d.status === 'expired') return ['⌛', 'Никто не принял', 'muted'];
    const win = d.winner_id === me;
    return [win ? '🏆' : '😔', win ? `+${fmt(d.prize - d.amount)}` : `−${fmt(d.amount)}`, win ? 'win' : 'lose'];
  }
  function duHistRow(d, idx) {
    const me = state.me && state.me.user_id;
    const [ic, txt, cls] = duResultText(d, me);
    const opp = d.creator_id === me ? (d.opponent_name || '—') : d.creator_name;
    return `<div class="tx" data-action="du-detail" data-idx="${idx}"><span class="ic">${ic}</span><div class="tt"><b>${d.game === 'coin' ? '🪙 Монетка' : '🎲 Кости'} · ставка ${fmt(d.amount)}</b><small>${d.status === 'done' ? 'против ' + esc(opp) + ' · ' : ''}${dt(d.finished_at || d.created_at)} · #${d.id}</small></div><div class="am ${cls}">${txt}</div></div>`;
  }
  async function duLoad() {
    if (DU.loading) return;
    DU.loading = true;
    try {
      const d = await api('/api/v2/duels');
      DU.cfg = d.config; DU.data = d;
      $('du-fee').textContent = `· комиссия ${String(d.config.fee_pct).replace('.', ',')}%`;
      duUpdatePrize();
      // мой вызов приняли — сообщаем результат
      const nowOpen = new Set(d.mine.map((x) => x.id));
      DU.mineOpen.forEach((id) => {
        if (nowOpen.has(id)) return;
        const h = d.history.find((x) => x.id === id);
        if (h && h.status === 'done') {
          const win = h.winner_id === (state.me && state.me.user_id);
          toast(win ? `⚔️ ${h.opponent_name} принял вызов — ты победил! +${fmt(h.prize)} PTS` : `⚔️ ${h.opponent_name} принял вызов и победил`, win ? 'ok' : 'err');
          M.loadMe().catch(() => {});
        }
      });
      DU.mineOpen = nowOpen;
      $('du-mine-wrap').classList.toggle('hidden', !d.mine.length);
      $('du-mine').innerHTML = d.mine.map(duMineCard).join('');
      $('du-count').textContent = d.open.length ? `· ${d.open.length}` : '';
      $('du-open').innerHTML = d.open.length ? d.open.map(duCard).join('') : '<div class="empty">Открытых вызовов пока нет.<br>Брось свой — его увидят все игроки ⚔️</div>';
      $('du-live-wrap').classList.toggle('hidden', !d.live.length);
      $('du-live').innerHTML = d.live.map((x) => `<span>${esc(x.winner_id === x.creator_id ? x.creator_name : x.opponent_name)} <b>+${short(x.prize)}</b> <small>${x.game === 'coin' ? '🪙' : '🎲'}</small></span>`).join('');
      $('du-hist-wrap').classList.toggle('hidden', !d.history.length);
      $('du-stats').textContent = d.stats.played ? `· побед ${d.stats.won} из ${d.stats.played}` : '';
      $('du-hist').innerHTML = d.history.map(duHistRow).join('');
    } catch (e) { $('du-open').innerHTML = `<div class="empty">${esc(e.message)}</div>`; } finally { DU.loading = false; }
  }
  M.tabs.duels = async () => {
    navTo('games');
    await duLoad();
    clearInterval(DU.timer);
    DU.timer = setInterval(() => { if (visible('duels')) duLoad(); else clearInterval(DU.timer); }, 5000);
  };
  M.actions['du-game'] = (el) => {
    DU.game = el.dataset.v;
    $$('#du-game .seg-btn').forEach((b) => b.classList.toggle('active', b === el));
    $('du-side-wrap').classList.toggle('hidden', DU.game !== 'coin');
    $('du-dice-note').classList.toggle('hidden', DU.game !== 'dice');
  };
  M.actions['du-side'] = (el) => { DU.side = el.dataset.v; $$('#du-side .seg-btn').forEach((b) => b.classList.toggle('active', b === el)); };
  M.actions['du-private'] = (el) => { DU.priv = !DU.priv; el.classList.toggle('on', DU.priv); };
  M.actions['du-create'] = async (el) => {
    let amount; try { amount = U.bet('du-bet'); } catch (e) { return fail(e); }
    el.disabled = true;
    try {
      const r = await post('/api/v2/duels/create', { game: DU.game, pick: DU.game === 'coin' ? DU.side : null, amount, private: DU.priv });
      U.setBal(r.balance);
      ok(DU.priv ? `Вызов создан. Код: ${r.duel.code} — отправь другу` : 'Вызов создан! Ждём соперника');
      DU.mineOpen.add(r.duel.id);
      await duLoad();
      if (DU.priv) duShare(r.duel.code, r.duel.amount, r.duel.game);
    } catch (e) { fail(e); } finally { el.disabled = false; }
  };
  M.actions['du-cancel'] = async (el) => {
    const yes = await dialog({ title: 'Отменить вызов?', text: 'Ставка вернётся на баланс.', okText: 'Отменить вызов', cancelText: 'Нет', danger: true });
    if (!yes) return;
    try { const r = await post('/api/v2/duels/cancel', { id: Number(el.dataset.id) }); U.setBal(r.balance); DU.mineOpen.delete(Number(el.dataset.id)); ok('Вызов отменён, ставка возвращена'); await duLoad(); } catch (e) { fail(e); await duLoad(); }
  };
  function duShare(code, amount, game) {
    const bot = (DU.cfg && DU.cfg.bot) || 'mspbetsbot';
    const link = `https://t.me/${bot}?startapp=duel_${code}`;
    const text = `⚔️ Вызываю тебя на дуэль в MSPBETS! ${game === 'coin' ? '🪙 Монетка' : '🎲 Кости'}, ставка ${fmt(amount)} PTS.\nКод дуэли: ${code} (Игры → Дуэли → «Есть код от друга?»)`;
    const url = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`;
    try { if (tg && tg.openTelegramLink) { tg.openTelegramLink(url); return; } } catch (_) {}
    U.copyText(`${text}\n${link}`).then((okk) => okk && ok('Приглашение скопировано'));
  }
  M.actions['du-share'] = (el) => duShare(el.dataset.code, Number(el.dataset.amount), el.dataset.game);
  M.actions['du-copy'] = async (el) => { if (await U.copyText(el.dataset.code)) ok('Код скопирован: ' + el.dataset.code); };
  async function duJoinFlow(d) {
    if (d.status !== 'open') return toast('Этот вызов уже принят или закрыт', 'err');
    if (state.me && d.creator_id === state.me.user_id) return toast('Это твой вызов — отправь код другу', 'err');
    const mySide = d.game === 'coin' ? (d.pick === 'heads' ? 'tails' : 'heads') : null;
    const yes = await dialog({
      title: `⚔️ Дуэль с ${d.creator_name}`,
      text: `${d.game === 'coin' ? `Монетка. Твоя сторона: ${SIDE[mySide]}.` : 'Кости: у кого сумма двух кубиков больше.'}\nСтавка: ${fmt(d.amount)} PTS. Победитель получит ${fmt(d.prize)} PTS.`,
      okText: `Принять · ${fmt(d.amount)}`, cancelText: 'Отмена',
    });
    if (!yes) return;
    try {
      const r = await post('/api/v2/duels/join', { code: d.code });
      await duShow(r.duel, r.win, mySide);
      U.after(r);
    } catch (e) { fail(e); }
    duLoad();
  }
  M.actions['du-join'] = (el) => {
    const d = DU.data && DU.data.open.find((x) => x.id === Number(el.dataset.id));
    if (d) duJoinFlow(d);
  };
  M.actions['du-find'] = async () => {
    const code = $('du-code').value.trim().toUpperCase();
    if (!code) return toast('Введите код', 'err');
    try { const d = await api('/api/v2/duels/code/' + encodeURIComponent(code)); $('du-code').value = ''; duJoinFlow(d); } catch (e) { fail(e); }
  };
  M.actions['du-detail'] = (el) => {
    const d = DU.data && DU.data.history[Number(el.dataset.idx)];
    if (!d) return;
    let res = '';
    if (d.status === 'done' && d.detail) {
      if (d.game === 'coin') res = `Выпало: ${SIDE[d.detail.side]} · сторона создателя: ${SIDE[d.pick]}`;
      else res = d.detail.rounds.map((r, i) => `${i ? 'Переброс' : 'Бросок'}: ${d.creator_name} ${r.creator.join('+')}=${r.creator[0] + r.creator[1]} vs ${d.opponent_name} ${r.opponent.join('+')}=${r.opponent[0] + r.opponent[1]}`).join('\n');
    }
    dialog({
      title: `Дуэль #${d.id}`,
      text: `${d.creator_name} vs ${d.opponent_name || '—'}\n${res}\n\nseed: ${d.seed || 'не раскрыт'}\nSHA-256: ${d.seed_hash}\nПроверка: HMAC-SHA256(seed, "${d.id}:${d.opponent_id || '?'}")`,
      okText: 'Ок', cancelText: 'Закрыть',
    });
  };
  async function duShow(d, win, mySide) {
    let m = $('du-modal');
    if (!m) {
      m = document.createElement('div');
      m.id = 'du-modal'; m.className = 'modal modal-center hidden'; m.dataset.action = 'du-close';
      m.innerHTML = '<div class="dialog du-res"><div class="du-stage" id="du-stage"></div><div class="du-res-t" id="du-res-t"></div><div class="du-res-s" id="du-res-s"></div><button class="btn btn-primary btn-block" type="button" data-action="du-close">Ок</button></div>';
      document.body.appendChild(m);
    }
    const stage = $('du-stage'), t = $('du-res-t'), s = $('du-res-s');
    t.textContent = ''; s.textContent = ''; t.className = 'du-res-t';
    m.classList.remove('hidden');
    if (d.game === 'coin') {
      stage.innerHTML = '<div class="du-coin spin" id="du-coin">🪙</div>';
      await sleep(1500);
      stage.innerHTML = `<div class="du-coin">${d.detail.side === 'heads' ? '🦅' : '👑'}</div><div class="du-cap">${SIDE[d.detail.side]}</div>`;
      s.textContent = `Твоя сторона: ${SIDE[mySide]}`;
    } else {
      const rounds = d.detail.rounds;
      for (let i = 0; i < rounds.length; i++) {
        stage.innerHTML = '<div class="du-dice"><div><small>Соперник</small><span class="roll">🎲🎲</span></div><div><small>Ты</small><span class="roll">🎲🎲</span></div></div>';
        await sleep(900);
        const r = rounds[i];
        stage.innerHTML = `<div class="du-dice"><div><small>${esc(d.creator_name)}</small><span>${DIE[r.creator[0]]}${DIE[r.creator[1]]}</span><b>${r.creator[0] + r.creator[1]}</b></div><div><small>Ты</small><span>${DIE[r.opponent[0]]}${DIE[r.opponent[1]]}</span><b>${r.opponent[0] + r.opponent[1]}</b></div></div>`;
        if (i < rounds.length - 1) { s.textContent = 'Ничья — переброс!'; await sleep(1100); s.textContent = ''; }
      }
    }
    t.textContent = win ? `Победа! +${fmt(d.prize)} PTS` : 'Поражение';
    t.classList.add(win ? 'win' : 'lose');
    haptic(win ? 'success' : 'error');
  }
  M.actions['du-close'] = (el, e) => { if (e && e.target !== el) return; $('du-modal').classList.add('hidden'); };

  // Ссылка-приглашение: t.me/<бот>?startapp=duel_КОД
  try {
    const sp = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    if (sp && /^duel_[A-Z0-9]{4,12}$/i.test(sp)) {
      setTimeout(async () => {
        await M.showTab('duels');
        try { duJoinFlow(await api('/api/v2/duels/code/' + encodeURIComponent(sp.slice(5)))); } catch (e) { fail(e); }
      }, 1200);
    }
  } catch (_) {}

  // ==========================================================
  //  ДОП. РЫНКИ НА МАТЧИ
  // ==========================================================
  const MK = { avail: {}, at: 0, loadingAvail: null, ev: null, sel: null, group: 'all' };
  function loadAvail(force) {
    if (!force && Date.now() - MK.at < 60000) return Promise.resolve(MK.avail);
    if (MK.loadingAvail) return MK.loadingAvail;
    MK.loadingAvail = api('/api/v2/markets/available').then((d) => { MK.avail = d || {}; MK.at = Date.now(); return MK.avail; })
      .catch(() => MK.avail).finally(() => { MK.loadingAvail = null; });
    return MK.loadingAvail;
  }
  function injectButtons() {
    $$('#event-list .match').forEach((card) => {
      if (card.querySelector('.mk-btn')) return;
      const n = MK.avail[card.dataset.id];
      if (!n) return;
      const odds = card.querySelector('.odds');
      if (!odds) return;
      odds.insertAdjacentHTML('afterend', `<button type="button" class="mk-btn" data-action="mk-open" data-id="${esc(card.dataset.id)}">📊 Тоталы, форы, точный счёт <b>+${n}</b></button>`);
    });
  }
  const list = $('event-list');
  if (list) {
    const run = debounce(async () => { await loadAvail(); injectButtons(); }, 60);
    new MutationObserver(run).observe(list, { childList: true, subtree: true });
    run();
  }

  function ensureMk() {
    let m = $('mk-modal');
    if (m) return m;
    m = document.createElement('div');
    m.id = 'mk-modal'; m.className = 'modal hidden'; m.dataset.action = 'mk-backdrop';
    m.innerHTML = `<div class="sheet tall mk-sheet" role="dialog" aria-modal="true" aria-label="Все рынки матча">
      <div class="sheet-head"><div class="sheet-title">📊 Все рынки</div><button class="icon-btn" type="button" data-action="mk-close" aria-label="Закрыть"><svg class="ico"><use href="#i-x"/></svg></button></div>
      <div id="mk-body"></div>
      <div id="mk-slip" class="mk-slip hidden">
        <div class="mk-slip-top"><div><small>Ваш выбор</small><b id="mk-sel-label"></b></div><div class="mk-slip-odd"><small>Кэф</small><b id="mk-sel-odds"></b></div><button class="icon-btn" type="button" data-action="mk-unsel" aria-label="Убрать"><svg class="ico"><use href="#i-x"/></svg></button></div>
        <div class="amount-row"><input id="mk-amount" class="input" type="number" min="1" value="100" inputmode="numeric">
          <button class="chip" type="button" data-action="mk-chip" data-v="500">500</button><button class="chip" type="button" data-action="mk-chip" data-v="1000">1K</button><button class="chip" type="button" data-action="mk-chip" data-v="max">MAX</button></div>
        <button class="btn btn-primary btn-block" type="button" id="mk-submit" data-action="mk-submit">Поставить</button>
      </div></div>`;
    document.body.appendChild(m);
    $('mk-amount').addEventListener('input', mkSlip);
    return m;
  }
  const ST = { pending: ['⏳', 'В игре'], won: ['✅', 'Выигрыш'], lost: ['❌', 'Проигрыш'], void: ['↩️', 'Возврат'] };
  function mkBetRow(b, withTitle) {
    const s = ST[b.status] || ['•', b.status];
    const right = b.status === 'won' ? `<div class="am win">+${fmt(b.payout)}</div>` : b.status === 'lost' ? `<div class="am lose">−${fmt(b.amount)}</div>` : b.status === 'void' ? `<div class="am">${fmt(b.amount)}</div>` : `<div class="am">${fmt(b.amount)}<small>выигрыш ${fmt(Math.floor(b.amount * b.odds))}</small></div>`;
    return `<div class="tx"><span class="ic">${s[0]}</span><div class="tt"><b>${esc(b.label)} · ${Number(b.odds).toFixed(2)}</b><small>${withTitle && b.title ? esc(b.title) + ' · ' : ''}${s[1]}${b.score ? ' · счёт ' + esc(b.score) : ''} · ${dt(b.created_at)}</small></div>${right}</div>`;
  }
  function mkRender() {
    const d = MK.ev;
    const body = $('mk-body');
    if (!d) { body.innerHTML = loading; return; }
    const teams = `<div class="mk-match"><b>${esc(d.home)}</b><i>vs</i><b>${esc(d.away)}</b><small>${esc(d.league || d.sport || '')}</small></div>`;
    if (!d.open) {
      body.innerHTML = teams + '<div class="empty">Приём ставок на этот матч закрыт</div>' + (d.my_bets.length ? '<div class="sub-head">Мои ставки на матч</div>' + d.my_bets.map((b) => mkBetRow(b)).join('') : '');
      return;
    }
    const tabs = [['all', 'Все']].concat(d.groups.map((g) => [g.key, g.title]));
    const groups = d.groups.filter((g) => MK.group === 'all' || g.key === MK.group);
    body.innerHTML = teams +
      `<div class="tabs mk-tabs">${tabs.map(([k, t]) => `<button class="chip${MK.group === k ? ' active' : ''}" type="button" data-action="mk-tab" data-k="${esc(k)}">${esc(t)}</button>`).join('')}</div>` +
      groups.map((g) => `<div class="mk-group"><div class="mk-gt">${esc(g.title)}</div><div class="mk-grid ${g.key === 'cs' ? 'c3' : g.key === 'dc' ? 'c3' : 'c2'}">${g.items.map((it) => {
        const sel = MK.sel && MK.sel.market === it.market && MK.sel.pick === it.pick;
        return `<button type="button" class="odd mk-odd${sel ? ' selected' : ''}" data-action="mk-pick" data-m="${esc(it.market)}" data-p="${esc(it.pick)}"><span>${esc(it.label)}</span><b>${Number(it.odds).toFixed(2)}</b></button>`;
      }).join('')}</div></div>`).join('') +
      (d.my_bets.length ? '<div class="sub-head">Мои ставки на матч</div>' + d.my_bets.map((b) => mkBetRow(b)).join('') : '') +
      `<div class="muted-note" style="margin-top:12px">Только одинары. ${esc(d.rules)} Все линии половинчатые — без возвратов. Коэффициенты рассчитаны из рыночной линии матча, маржа ${String(d.margin).replace('.', ',')}%.</div>`;
  }
  function mkSlip() {
    const s = MK.sel, box = $('mk-slip');
    if (!s) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    $('mk-sel-label').textContent = s.label;
    $('mk-sel-odds').textContent = Number(s.odds).toFixed(2);
    const a = Math.floor(Number($('mk-amount').value)) || 0;
    $('mk-submit').textContent = a >= 1 ? `Поставить ${fmt(a)} · выигрыш ${fmt(Math.floor(a * s.odds))}` : 'Поставить';
  }
  async function mkLoad(id, keepSel) {
    const d = await api('/api/v2/markets/event/' + id);
    MK.ev = d;
    if (keepSel && MK.sel) {
      const it = d.groups.flatMap((g) => g.items).find((x) => x.market === MK.sel.market && x.pick === MK.sel.pick);
      MK.sel = it ? { ...it } : null;
    }
    mkRender(); mkSlip();
  }
  M.actions['mk-open'] = async (el) => {
    haptic('light');
    ensureMk().classList.remove('hidden');
    MK.ev = null; MK.sel = null; MK.group = 'all';
    mkRender(); mkSlip();
    try { await mkLoad(Number(el.dataset.id)); } catch (e) { $('mk-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  };
  const mkClose = () => { const m = $('mk-modal'); if (m) m.classList.add('hidden'); };
  M.actions['mk-close'] = mkClose;
  M.actions['mk-backdrop'] = (el, e) => { if (e.target === el) mkClose(); };
  M.actions['mk-tab'] = (el) => { MK.group = el.dataset.k; mkRender(); };
  M.actions['mk-pick'] = (el) => {
    const it = MK.ev && MK.ev.groups.flatMap((g) => g.items).find((x) => x.market === el.dataset.m && x.pick === el.dataset.p);
    if (!it) return;
    const same = MK.sel && MK.sel.market === it.market && MK.sel.pick === it.pick;
    MK.sel = same ? null : { ...it };
    haptic('light'); mkRender(); mkSlip();
  };
  M.actions['mk-unsel'] = () => { MK.sel = null; mkRender(); mkSlip(); };
  M.actions['mk-chip'] = (el) => { $('mk-amount').value = el.dataset.v === 'max' ? Math.max(1, Math.floor((state.me && state.me.balance) || 1)) : el.dataset.v; mkSlip(); };
  M.actions['mk-submit'] = async () => {
    const s = MK.sel, d = MK.ev;
    const amount = Math.floor(Number($('mk-amount').value));
    if (!s || !d) return;
    if (!(amount >= 1)) return toast('Введите корректную сумму', 'err');
    const btn = $('mk-submit'); btn.disabled = true;
    try {
      const r = await post('/api/v2/markets/bet', { event_id: d.event_id, market: s.market, pick: s.pick, amount, odds: s.odds });
      ok(`Ставка принята: ${r.label} · ${Number(r.odds).toFixed(2)}`);
      U.after(r);
      MK.sel = null;
      await mkLoad(d.event_id);
    } catch (e) {
      fail(e);
      if (/Коэффициент изменился/.test(e.message)) { try { await mkLoad(d.event_id, true); } catch (_) {} }
    } finally { btn.disabled = false; mkSlip(); }
  };

  // Раздел «Ставки на рынки» (Профиль → меню, История ставок → ссылка)
  panel('mk-bets', back('profile', 'Профиль') + head('Ставки на рынки', 'Тоталы, форы, точный счёт и другие рынки') + '<div id="mk-bets-body"></div>');
  M.tabs['mk-bets'] = async () => {
    navTo('profile');
    $('mk-bets-body').innerHTML = loading;
    const d = await api('/api/v2/markets/my');
    const st = d.stats;
    $('mk-bets-body').innerHTML = (st.count ? `<div class="gcard"><div class="stat-row"><span>Ставок</span><b>${fmt(st.count)} · выиграно ${fmt(st.won)}</b></div><div class="stat-row"><span>Поставлено</span><b>${fmt(st.staked)} PTS</b></div><div class="stat-row"><span>Выплачено</span><b>${fmt(st.returned)} PTS</b></div></div>` : '') +
      (d.bets.length ? d.bets.map((b) => mkBetRow(b, true)).join('') : '<div class="empty">Ставок на рынки пока нет.<br>Открой матч и нажми «📊 Тоталы, форы, точный счёт».</div>');
  };
  const histHead = document.querySelector('#history .panel-head');
  if (histHead) histHead.insertAdjacentHTML('afterend', '<button type="button" class="mk-link" data-tab="mk-bets">📊 Ставки на тоталы, форы и точный счёт →</button>');

  M.tabs.profile = ((orig) => async () => {
    if (orig) await orig();
    const menu = document.querySelector('#profile-body .menu');
    if (menu && !menu.querySelector('[data-tab="mk-bets"]')) {
      const h = menu.querySelector('[data-tab="history"]');
      const html = '<button data-tab="mk-bets"><span class="i">📊</span>Ставки на рынки<small>тоталы, форы, счёт</small></button><button data-tab="duels"><span class="i">⚔️</span>Дуэли 1 на 1<small>вызови друга</small></button>';
      if (h) h.insertAdjacentHTML('afterend', html); else menu.insertAdjacentHTML('afterbegin', html);
    }
  })(M.tabs.profile);

  // ==========================================================
  //  БОНУСЫ: VIP-кэшбэк и рейкбек
  // ==========================================================
  const bonusBody = $('bonus-body');
  if (bonusBody) bonusBody.insertAdjacentHTML('afterend', '<div id="vb-body"></div>');
  const pct = (x) => String(Math.round(x * 10000) / 100).replace('.', ',') + '%';
  async function vbRender() {
    const box = $('vb-body'); if (!box) return;
    try {
      const d = await api('/api/v2/vipback');
      const rb = d.rakeback, cb = d.cashback, cur = cb.current;
      const daysLeft = Math.max(0, Math.ceil((new Date(cb.deadline) - Date.now()) / 864e5));
      box.innerHTML = `
        <div class="gcard vb">
          <div class="vb-head"><div><div class="label">VIP-кэшбэк и рейкбек</div><b class="vb-lvl">${d.vip ? `<span class="vip">VIP ${d.vip}</span>` : 'Без VIP'} <small>· уровень ${d.level}</small></b></div><button class="jp-info" type="button" data-action="vb-info">Что это?</button></div>
          <div class="vb-grid">
            <div class="vb-box"><small>♻️ Рейкбек ${pct(rb.rate)}</small><b>${fmt(rb.available)}</b><span>с каждой ставки · копится всегда</span>
              <button class="btn btn-sm ${rb.available >= 1 ? 'btn-success' : 'btn-ghost'}" type="button" data-action="vb-rb" ${rb.available >= 1 ? '' : 'disabled'}>Забрать</button></div>
            <div class="vb-box"><small>💸 Кэшбэк ${pct(cb.rate)} · ${esc(cb.week_label)}</small><b>${fmt(cb.claimed ? cb.claimed_amount : cb.amount)}</b><span>${cb.loss ? `проигрыш за неделю ${fmt(cb.loss)}` : 'за прошлую неделю проигрыша нет'}</span>
              ${cb.claimed ? '<button class="btn btn-sm btn-ghost" type="button" disabled>Получено ✓</button>' : `<button class="btn btn-sm ${cb.amount >= 1 ? 'btn-success' : 'btn-ghost'}" type="button" data-action="vb-cb" ${cb.amount >= 1 ? '' : 'disabled'}>Забрать</button>`}</div>
          </div>
          <div class="muted-note" style="margin:10px 0 0">${!cb.claimed && cb.amount >= 1 ? `Кэшбэк за прошлую неделю можно забрать ещё ${daysLeft} дн. ` : ''}Эта неделя: ${cur.loss ? `проигрыш ${fmt(cur.loss)} → кэшбэк ≈ <b>${fmt(cur.estimate)}</b> с понедельника` : 'пока в плюсе — кэшбэк не нужен 😉'}.</div>
          <details class="vb-table"><summary>Проценты по VIP-уровням</summary>
            <table><tr><th>VIP</th><th>Уровень</th><th>Рейкбек</th><th>Кэшбэк</th><th>Макс.</th></tr>
            ${d.table.map((r) => `<tr class="${r.vip === d.vip ? 'on' : ''}"><td>${r.vip || '—'}</td><td>${r.level}+</td><td>${pct(r.rakeback)}</td><td>${pct(r.cashback)}</td><td>${short(r.cap)}</td></tr>`).join('')}</table></details>
        </div>`;
    } catch (e) { box.innerHTML = ''; }
  }
  M.tabs.bonus = ((orig) => async () => { const p = vbRender(); if (orig) await orig(); await p; })(M.tabs.bonus);
  M.actions['vb-rb'] = async (el) => { el.disabled = true; try { const r = await post('/api/v2/vipback/rakeback'); ok(`Рейкбек +${fmt(r.amount)} PTS`); U.setBal(r.balance); } catch (e) { fail(e); } vbRender(); };
  M.actions['vb-cb'] = async (el) => { el.disabled = true; try { const r = await post('/api/v2/vipback/cashback'); ok(`Кэшбэк +${fmt(r.amount)} PTS`); U.setBal(r.balance); } catch (e) { fail(e); } vbRender(); };
  M.actions['vb-info'] = () => dialog({
    title: 'VIP-кэшбэк и рейкбек',
    text: 'Рейкбек — процент от каждой твоей ставки (на матчи, в играх, кейсах), выиграла она или нет. Копится постоянно, забрать можно в любой момент.\n\nКэшбэк — раз в неделю возвращаем процент от чистого проигрыша прошлой недели (пн–вс). Забрать можно до конца следующей недели.\n\nЧем выше VIP-уровень, тем больше проценты: VIP 1 с 20 уровня, VIP 4 — с 60.',
    okText: 'Понятно', cancelText: 'Закрыть',
  });

  // ==========================================================
  //  ЖУРНАЛ: подписи для новых видов операций
  // ==========================================================
  const KIND = { jackpot: ['💰', 'Джекпот'], rakeback: ['♻️', 'Рейкбек'], cashback: ['💸', 'Кэшбэк'] };
  const ledger = $('ledger-list');
  if (ledger) {
    const fix = () => $$('#ledger-list .tx').forEach((tx) => {
      if (tx.dataset.plus) return;
      const small = tx.querySelector('.tt small'), ic = tx.querySelector('.ic');
      if (!small || !ic) return;
      const k = Object.keys(KIND).find((x) => small.textContent.startsWith(x + ' ·'));
      tx.dataset.plus = '1';
      if (k) { ic.textContent = KIND[k][0]; small.textContent = small.textContent.replace(k, KIND[k][1]); }
    });
    new MutationObserver(fix).observe(ledger, { childList: true });
  }
})();
