/* ==========================================================
   MSPBETS — игры: Краш, Мины, Plinko, Dice, Колесо, Монетка.
   Клиент только рисует: ставки, броски и результаты решает сервер.
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M || !M.U) return;
  const { api, post, toast, ok, fail, dialog, fmt, esc, haptic, sleep, state, U } = M;
  const $ = (id) => document.getElementById(id);
  const main = document.querySelector('main.main');
  const anchor = $('history');
  const panel = (id, html) => { const s = document.createElement('section'); s.id = id; s.className = 'panel hidden'; s.innerHTML = html; main.insertBefore(s, anchor); };
  const head = (t, p) => `<button class="back" type="button" data-tab="games">← Игры</button><div class="panel-head"><h2>${t}</h2><p>${p}</p></div>`;
  const x2 = (n) => Number(n).toFixed(2);
  const colorOf = (m) => (m < 1 ? '#FF5D7A' : m < 2 ? '#FFA43C' : m < 5 ? '#FFC93C' : m < 30 ? '#2EE59D' : '#B26BFF');
  let GC = null; // конфиг игр (таблицы множителей)
  const config = async () => GC || (GC = await api('/api/v2/games/config'));
  const leaveHooks = {};
  M.leave = (name) => Object.keys(leaveHooks).forEach((k) => { if (k !== name) leaveHooks[k](); });
  const meId = () => state.me && state.me.user_id;

  // ==========================================================
  //  КРАШ (обычный: раунд идёт, только когда игрок сам нажал «Играть»)
  // ==========================================================
  panel('crash', head('Краш', 'Нажми «Играть» и забери выигрыш до крэша') + `
    <div class="cr-hist" id="cr-hist"></div>
    <div class="cr-stage"><canvas id="cr-canvas"></canvas><div class="cr-center"><div class="cr-mult" id="cr-mult">1.00x</div><div class="cr-status" id="cr-status">Сделай ставку и нажми «Играть»</div></div></div>
    <div class="gcard" style="margin-top:12px">
      ${U.betBox('cr-bet', 100)}
      <div style="margin-top:8px"><label class="label" for="cr-auto">Автовывод (x)</label><input id="cr-auto" class="input" type="number" step="0.01" min="1.01" placeholder="необязательно, например 2.00"></div>
      <button id="cr-btn" class="btn btn-primary btn-block btn-lg" style="margin-top:12px" data-action="cr-act">Играть</button>
      <div class="rtp-note">RTP 96% · шанс множителя ≥ x равен 0,96 / x</div></div>
    <div class="fair" id="cr-fair"></div>`);

  // st: null | { phase: 'running'|'crashed'|'cashed', start, growth, bet, auto, mult, payout, crash_point, seed, seed_hash, round_id }
  const CR = { st: null, off: 0, raf: 0, active: false, busy: false, curve: [], head: null, last: 0, txt: {}, sig: {}, W: 0, H: 0, dpr: 1, ro: null, g: null, wait: 0 };
  const setTxt = (key, el, v) => { if (CR.txt[key] !== v) { CR.txt[key] = v; el.textContent = v; } };
  const setCls = (key, el, v) => { if (CR.txt[key] !== v) { CR.txt[key] = v; el.className = v; } };
  const setHtml = (key, el, v) => { if (CR.sig[key] !== v) { CR.sig[key] = v; el.innerHTML = v; } };
  function crSize() { const c = $('cr-canvas'); if (!c) return; const r = c.getBoundingClientRect(); CR.dpr = Math.min(devicePixelRatio || 1, 2); CR.W = Math.round(r.width * CR.dpr); CR.H = Math.round(r.height * CR.dpr); if (CR.W && (c.width !== CR.W || c.height !== CR.H)) { c.width = CR.W; c.height = CR.H; } }
  const crNow = () => Date.now() / 1000 + CR.off;
  const crRunning = () => !!CR.st && CR.st.phase === 'running';
  const crMult = () => { const s = CR.st; return s && s.phase === 'running' ? Math.floor(Math.exp(s.growth * Math.max(0, crNow() - s.start)) * 100) / 100 : 1; };

  function crSet(st) {
    if (st && st.server_time) CR.off = st.server_time - Date.now() / 1000;
    const prev = CR.st; CR.st = st;
    if (st && st.phase === 'running' && (!prev || prev.round_id !== st.round_id)) {
      CR.curve = []; CR.head = null;
      for (let t = 0; t < crNow() - st.start - 0.2; t += 0.12) CR.curve.push({ t, m: Math.exp(st.growth * t) }); // вернулись посреди раунда — дорисовать график
      crWait(st.round_id);
    }
    if (st && st.phase !== 'running') CR.wait = 0;
    crInfo(); crButton();
  }
  function crInfo() {
    const s = CR.st;
    setHtml('fair', $('cr-fair'), !s ? '' : s.phase !== 'running' && s.seed ? `Раунд #${s.round_id} · seed: ${s.seed}<br>hash до раунда: ${s.seed_hash}<br>Проверка: crash = HMAC-SHA256(seed, "mspbets-crash")` : `Раунд #${s.round_id} · хеш seed (раскроется после раунда): ${s.seed_hash}`);
  }
  async function crHist() {
    try {
      const rows = await api('/api/v2/crash/rounds');
      setHtml('hist', $('cr-hist'), rows.slice().reverse().map((h) => `<span class="${h.crash_point >= 10 ? 'hi' : h.crash_point >= 2 ? 'mid' : 'lo'}">${x2(h.crash_point)}x</span>`).join(''));
    } catch (_) {}
  }
  // long-poll: сервер отвечает ровно в момент краша/автовывода (точку краша клиент заранее не знает)
  async function crWait(id) {
    CR.wait = id;
    while (CR.active && CR.wait === id && crRunning() && CR.st.round_id === id) {
      try {
        const d = await api('/api/v2/crash/wait?round=' + id);
        if (CR.wait !== id) return;
        if (d.phase && d.phase !== 'running') { crFinish(d); return; }
        if (d.phase === 'idle') return;
      } catch (_) { await sleep(1500); }
    }
  }
  function crFinish(d) {
    crSet(d); crHist(); U.touchMe();
    if (d.phase === 'crashed') haptic('error');
    else if (d.phase === 'cashed') { haptic('success'); ok(`Автовывод x${x2(d.mult)} · +${fmt(d.payout)} PTS`); M.loadMe().catch(() => {}); if (d.unlocked && d.unlocked.length) U.popups(d.unlocked); }
  }
  function crButton() {
    const s = CR.st, b = $('cr-btn'); if (!b) return;
    const run = crRunning();
    setCls('btncls', b, 'btn btn-block btn-lg ' + (run ? 'btn-warn' : 'btn-primary'));
    const label = run ? `Забрать ${fmt(Math.floor(s.bet * crMult()))} PTS` : s ? 'Играть снова' : 'Играть';
    if (b.disabled !== CR.busy) b.disabled = CR.busy;
    setTxt('btn', b, label);
  }
  function crLoop(ts) {
    if (!CR.active) return; CR.raf = requestAnimationFrame(crLoop);
    if (ts - CR.last < 32) return; CR.last = ts;
    const s = CR.st; const mEl = $('cr-mult'), stEl = $('cr-status'); if (!mEl) return;
    let mode = 'idle';
    if (!s) { setTxt('m', mEl, '1.00x'); setCls('mc', mEl, 'cr-mult'); setTxt('st', stEl, 'Сделай ставку и нажми «Играть»'); }
    else if (s.phase === 'running') {
      const m = crMult(); const t = crNow() - s.start;
      setTxt('m', mEl, x2(m) + 'x'); setCls('mc', mEl, 'cr-mult'); setTxt('st', stEl, s.auto ? `Автовывод на ${x2(s.auto)}x · жми «Забрать», пока не поздно!` : 'Жми «Забрать», пока не поздно!');
      const lt = CR.curve.length ? CR.curve[CR.curve.length - 1].t : -1;
      if (t - lt >= 0.12) { CR.curve.push({ t, m }); if (CR.curve.length > 700) CR.curve = CR.curve.filter((_, i) => i % 2 === 0); }
      CR.head = { t, m }; crButton(); mode = 'live';
    } else if (s.phase === 'cashed') { setTxt('m', mEl, x2(s.mult) + 'x'); setCls('mc', mEl, 'cr-mult won'); setTxt('st', stEl, `Вы забрали x${x2(s.mult)} · +${fmt(s.payout)} PTS`); mode = 'cashed'; }
    else { setTxt('m', mEl, x2(s.crash_point || 1) + 'x'); setCls('mc', mEl, 'cr-mult crashed'); setTxt('st', stEl, `Крэш! Ставка ${fmt(s.bet)} PTS сгорела`); mode = 'crashed'; }
    crDraw(mode, s);
  }
  function crDraw(mode, s) {
    const c = $('cr-canvas'); if (!CR.W) crSize(); if (!CR.W) return; const dpr = CR.dpr, W = CR.W, H = CR.H;
    const g = CR.g || (CR.g = c.getContext('2d')); g.clearRect(0, 0, W, H);
    const pad = { l: 40 * dpr, r: 14 * dpr, t: 16 * dpr, b: 18 * dpr }; const pts = CR.curve; const last = CR.head || pts[pts.length - 1] || { t: 0, m: 1 };
    const endM = mode === 'crashed' ? (s.crash_point || last.m) : last.m;
    const tMax = Math.max(8, last.t * 1.12), mMax = Math.max(2.2, Math.max(endM, last.m) * 1.18); const gw = W - pad.l - pad.r, gh = H - pad.t - pad.b;
    const lm = Math.log(mMax), X = (t) => pad.l + gw * t / tMax, Y = (m) => H - pad.b - gh * (Math.log(Math.max(1, m)) / lm);
    g.font = `${10 * dpr}px Manrope, sans-serif`; g.textBaseline = 'middle';
    [1.5, 2, 3, 5, 10, 25, 100].filter((v) => v < mMax).forEach((v) => { g.strokeStyle = 'rgba(255,255,255,.06)'; g.lineWidth = dpr; g.beginPath(); g.moveTo(pad.l, Y(v)); g.lineTo(W - pad.r, Y(v)); g.stroke(); g.fillStyle = 'rgba(139,144,171,.85)'; g.fillText(v + 'x', 4 * dpr, Y(v)); });
    const n = pts.length + (CR.head ? 1 : 0); if (n < 2) return;
    const col = mode === 'crashed' ? '#FF5D7A' : mode === 'cashed' ? '#2EE59D' : '#22D3EE';
    const grad = g.createLinearGradient(0, pad.t, 0, H - pad.b); grad.addColorStop(0, col + '55'); grad.addColorStop(1, col + '00');
    const trace = () => { let i = 0; for (const p of pts) { i ? g.lineTo(X(p.t), Y(p.m)) : g.moveTo(X(p.t), Y(p.m)); i++; } if (CR.head) (i ? g.lineTo(X(CR.head.t), Y(CR.head.m)) : g.moveTo(X(CR.head.t), Y(CR.head.m))); };
    g.beginPath(); trace(); g.lineTo(X(last.t), H - pad.b); g.lineTo(X(0), H - pad.b); g.closePath(); g.fillStyle = grad; g.fill();
    g.beginPath(); trace(); g.strokeStyle = col; g.lineWidth = 3 * dpr; g.lineJoin = 'round'; g.stroke();
    g.beginPath(); g.arc(X(last.t), Y(last.m), 11 * dpr, 0, 7); g.fillStyle = col + '33'; g.fill(); g.beginPath(); g.arc(X(last.t), Y(last.m), 5 * dpr, 0, 7); g.fillStyle = '#fff'; g.fill();
  }
  function crStop() { CR.active = false; CR.wait = 0; cancelAnimationFrame(CR.raf); if (CR.ro) { CR.ro.disconnect(); CR.ro = null; } }
  leaveHooks.crash = crStop;
  M.tabs.crash = async () => {
    crStop(); CR.active = true; CR.st = null; CR.head = null; CR.curve = []; CR.sig = {}; CR.txt = {}; CR.g = null; CR.W = 0; CR.busy = false;
    crSize(); if (window.ResizeObserver) { CR.ro = new ResizeObserver(crSize); CR.ro.observe($('cr-canvas')); }
    crButton(); CR.raf = requestAnimationFrame(crLoop); crHist();
    try { const d = await api('/api/v2/crash/state'); if (CR.active && d.phase === 'running') { crSet(d); } } catch (_) {} // вернулись в игру посреди раунда
  };
  M.actions['cr-act'] = async () => {
    if (CR.busy) return; CR.busy = true; crButton();
    try {
      if (crRunning()) {
        const id = CR.st.round_id;
        try {
          const r = await post('/api/v2/crash/cashout'); CR.wait = 0;
          crSet({ ...r, server_time: r.server_time }); ok(`Забрали x${x2(r.mult)} · +${fmt(r.payout)} PTS`); haptic('success'); U.after(r); crHist();
        } catch (e) {
          // раунд мог закончиться, пока шёл запрос — узнаём итог у сервера
          const d = await api('/api/v2/crash/state').catch(() => null);
          if (d && d.round_id === id && d.phase !== 'running') { CR.busy = false; crFinish(d); } else throw e;
        }
      } else {
        const bet = U.bet('cr-bet'); const auto = Number($('cr-auto').value) || null;
        CR.curve = []; CR.head = null;
        const r = await post('/api/v2/crash/start', { bet, auto }); haptic('medium'); U.after(r); crSet(r);
      }
    } catch (e) { fail(e); } finally { CR.busy = false; crButton(); }
  };

  // ==========================================================
  //  МИНЫ
  // ==========================================================
  panel('mines', head('Мины', 'Открывай ячейки, избегай мин, забирай в любой момент · RTP 97%') + `
    <div class="gcard"><div class="mines-info"><div><small>Множитель</small><b id="mn-mult">1.00x</b></div><div><small>Следующий</small><b id="mn-next">—</b></div><div><small>Забрать</small><b id="mn-cash">0</b></div></div>
      <div class="mines-grid" id="mn-grid"></div><div class="res-pop" id="mn-res"></div>
      <div id="mn-setup">${U.betBox('mn-bet', 100)}<label class="label" style="margin-top:10px">Количество мин: <b id="mn-mc">3</b></label>
        <input class="range" id="mn-mines" type="range" min="1" max="24" value="3"><button class="btn btn-primary btn-block btn-lg" data-action="mn-start">Начать игру</button></div>
      <button class="btn btn-warn btn-block btn-lg hidden" id="mn-cashout" data-action="mn-cash">Забрать</button></div>`);
  const MN = { st: null, busy: false, over: null };
  const mnMult = (k, m) => { let p = 0.97; for (let i = 0; i < k; i++) p *= (25 - i) / (25 - m - i); return Math.floor(p * 100) / 100; };
  function mnRender() {
    const st = MN.st, o = MN.over; const rev = st ? st.revealed : o ? o.revealed : [];
    $('mn-grid').innerHTML = Array.from({ length: 25 }, (_, i) => {
      let cls = 'mcell', txt = '';
      if (rev.includes(i)) { cls += ' safe'; txt = '💎'; }
      if (o && o.positions.includes(i)) { cls = 'mcell mine' + (o.hit === i ? '' : ' dim'); txt = '💣'; }
      return `<button class="${cls}" data-action="mn-open" data-i="${i}" ${!st || rev.includes(i) || MN.busy ? 'disabled' : ''}>${txt}</button>`;
    }).join('');
    $('mn-setup').classList.toggle('hidden', !!st); $('mn-cashout').classList.toggle('hidden', !st);
    $('mn-mult').textContent = st ? x2(st.mult) + 'x' : '1.00x'; $('mn-next').textContent = st ? x2(st.next_mult) + 'x' : '—'; $('mn-cash').textContent = st ? fmt(st.cashout) : '0';
    $('mn-cashout').disabled = !st || !st.revealed.length; $('mn-cashout').textContent = st && st.revealed.length ? `Забрать ${fmt(st.cashout)} PTS` : 'Открой хотя бы одну ячейку';
  }
  M.tabs.mines = async () => { const d = await api('/api/v2/games/mines/state'); MN.st = d.active ? d : null; MN.over = null; $('mn-res').textContent = ''; mnRender(); };
  $('mn-mines').addEventListener('input', (e) => { $('mn-mc').textContent = e.target.value; });
  M.actions['mn-start'] = async () => { try { const r = await post('/api/v2/games/mines/start', { bet: U.bet('mn-bet'), mines: Number($('mn-mines').value) }); MN.st = r.state; MN.over = null; $('mn-res').textContent = ''; U.after(r); mnRender(); haptic('medium'); } catch (e) { fail(e); } };
  M.actions['mn-open'] = async (el) => {
    if (MN.busy || !MN.st) return; MN.busy = true;
    try {
      const r = await post('/api/v2/games/mines/reveal', { index: Number(el.dataset.i) }); haptic(r.boom ? 'error' : 'light');
      if (r.boom) { MN.over = { positions: r.positions, revealed: r.revealed, hit: Number(el.dataset.i) }; MN.st = null; $('mn-res').className = 'res-pop lose'; $('mn-res').textContent = 'БУМ! Ставка сгорела'; U.after(r); }
      else if (r.done) { MN.over = { positions: r.positions, revealed: r.revealed, hit: -1 }; MN.st = null; $('mn-res').className = 'res-pop win'; $('mn-res').textContent = `Поле очищено! +${fmt(r.payout)} PTS`; U.after(r); }
      else MN.st = r.state;
      if (r.unlocked && r.unlocked.length) U.popups(r.unlocked);
    } catch (e) { fail(e); if (/активной/.test(e.message)) { MN.st = null; } }
    finally { MN.busy = false; mnRender(); }
  };
  M.actions['mn-cash'] = async () => { try { const r = await post('/api/v2/games/mines/cashout'); MN.over = { positions: r.positions, revealed: r.revealed, hit: -1 }; MN.st = null; $('mn-res').className = 'res-pop win'; $('mn-res').textContent = `+${fmt(r.payout)} PTS (x${x2(r.mult)})`; U.after(r); haptic('success'); mnRender(); } catch (e) { fail(e); } };

  // ==========================================================
  //  PLINKO
  // ==========================================================
  panel('plinko', head('Plinko', 'Шарик падает по колышкам · чем дальше от центра, тем больше множитель') + `
    <div class="plinko-wrap"><canvas id="pl-canvas" width="720" height="660"></canvas><div class="pl-mults" id="pl-mults"></div></div>
    <div class="gcard" style="margin-top:12px"><div class="label">Ряды</div><div class="seg3" id="pl-rows">${[8, 10, 12, 14, 16].map((r) => `<button class="seg-btn ${r === 12 ? 'active' : ''}" data-action="pl-rows" data-v="${r}">${r}</button>`).join('')}</div>
      <div class="label" style="margin-top:10px">Риск</div><div class="seg3" id="pl-risk"><button class="seg-btn" data-action="pl-risk" data-v="low">Низкий</button><button class="seg-btn active" data-action="pl-risk" data-v="medium">Средний</button><button class="seg-btn" data-action="pl-risk" data-v="high">Высокий</button></div>
      <div style="margin-top:10px">${U.betBox('pl-bet', 100)}</div><div class="res-pop" id="pl-res"></div>
      <button class="btn btn-primary btn-block btn-lg" id="pl-go" data-action="pl-drop">Бросить шарик</button><div class="rtp-note" id="pl-rtp"></div></div>`);
  const PL = { rows: 12, risk: 'medium', busy: false, balls: [] };
  function plLayout() {
    const W = 720, top = 40, bottom = 640, dy = (bottom - top) / PL.rows, dx = (W - 80) / (PL.rows + 2);
    return { W, top, dy, dx, peg: (i, j) => ({ x: W / 2 + (j - (i + 2) / 2) * dx, y: top + i * dy }) };
  }
  // колышки рисуются один раз в offscreen-canvas и потом просто копируются (раньше — ~100 дуг на каждый кадр)
  function plPegs() {
    if (PL.pegCache && PL.pegCache.rows === PL.rows) return PL.pegCache.cv;
    const cv = document.createElement('canvas'); cv.width = 720; cv.height = 660; const g = cv.getContext('2d'), L = plLayout();
    g.fillStyle = 'rgba(255,255,255,.85)'; g.beginPath();
    for (let i = 0; i < PL.rows; i++) for (let j = 0; j < i + 3; j++) { const p = L.peg(i, j); g.moveTo(p.x + 6, p.y); g.arc(p.x, p.y, 6, 0, 7); }
    g.fill(); PL.pegCache = { rows: PL.rows, cv }; return cv;
  }
  function plDraw(ballsOn) {
    const c = $('pl-canvas'), g = PL.g || (PL.g = c.getContext('2d')); g.clearRect(0, 0, 720, 660); g.drawImage(plPegs(), 0, 0);
    (ballsOn || []).forEach((b) => { g.beginPath(); g.arc(b.x, b.y, 22, 0, 7); g.fillStyle = 'rgba(255,201,60,.25)'; g.fill(); g.beginPath(); g.arc(b.x, b.y, 13, 0, 7); g.fillStyle = '#FFC93C'; g.fill(); });
  }
  async function plRender() {
    const cfg = (await config()).plinko[PL.rows][PL.risk];
    $('pl-mults').innerHTML = cfg.mult.map((m, i) => `<span data-i="${i}" style="background:${colorOf(m)}">${m >= 100 ? Math.round(m) : m}x</span>`).join('');
    $('pl-rtp').textContent = `RTP ${cfg.rtp}% · шанс крайней ячейки ${cfg.prob[0]}%`; plDraw();
  }
  M.tabs.plinko = plRender;
  M.actions['pl-rows'] = (el) => { if (PL.busy) return; PL.rows = Number(el.dataset.v); document.querySelectorAll('#pl-rows .seg-btn').forEach((b) => b.classList.toggle('active', b === el)); plRender(); };
  M.actions['pl-risk'] = (el) => { if (PL.busy) return; PL.risk = el.dataset.v; document.querySelectorAll('#pl-risk .seg-btn').forEach((b) => b.classList.toggle('active', b === el)); plRender(); };
  M.actions['pl-drop'] = async () => {
    if (PL.busy) return; PL.busy = true; $('pl-go').disabled = true; $('pl-res').textContent = '';
    try {
      const r = await post('/api/v2/games/plinko', { bet: U.bet('pl-bet'), rows: PL.rows, risk: PL.risk }); const L = plLayout();
      let off = 0; const pos = [{ x: L.W / 2, y: 14 }];
      for (let i = 0; i < PL.rows; i++) { const p = L.peg(i, 0); off += r.path[i] ? 0.5 : -0.5; pos.push({ x: L.W / 2 + off * L.dx, y: L.top + i * L.dy + L.dy * 0.55 }); }
      pos.push({ x: L.W / 2 + off * L.dx, y: 652 });
      for (let k = 1; k < pos.length; k++) {
        const a = pos[k - 1], b = pos[k], t0 = performance.now(), dur = k === pos.length - 1 ? 220 : 150;
        await new Promise((res) => { const f = (now) => { const t = Math.min(1, (now - t0) / dur); plDraw([{ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t - Math.sin(t * Math.PI) * 14 }]); t < 1 ? requestAnimationFrame(f) : res(); }; requestAnimationFrame(f); });
        haptic('light');
      }
      plDraw(); const cell = document.querySelector(`#pl-mults span[data-i="${r.slot}"]`); if (cell) { cell.classList.add('hit'); setTimeout(() => cell.classList.remove('hit'), 1400); }
      $('pl-res').className = 'res-pop ' + (r.payout >= r.mult * 0 && r.mult >= 1 ? 'win' : 'lose'); $('pl-res').textContent = `x${r.mult} · ${r.payout > 0 ? '+' : ''}${fmt(r.payout)} PTS`; U.after(r);
    } catch (e) { fail(e); } finally { PL.busy = false; $('pl-go').disabled = false; }
  };

  // ==========================================================
  //  DICE
  // ==========================================================
  panel('dice', head('Dice', 'Выбери шанс и направление · RTP 97%') + `
    <div class="gcard"><div class="dice-board"><div class="dice-roll" id="dc-roll">50.00</div><div class="dice-track" id="dc-track"><i id="dc-win"></i><b id="dc-mark" style="left:50%"></b></div></div>
      <div class="seg" style="margin-bottom:8px"><button class="seg-btn active" data-action="dc-dir" data-v="under">Меньше</button><button class="seg-btn" data-action="dc-dir" data-v="over">Больше</button></div>
      <input class="range" id="dc-chance" type="range" min="1" max="95" step="0.5" value="50">
      <div class="mines-info"><div><small>Шанс</small><b id="dc-ch">50%</b></div><div><small>Множитель</small><b id="dc-mult">1.94x</b></div><div><small>Порог</small><b id="dc-thr">< 50.00</b></div></div>
      <div style="margin-top:12px">${U.betBox('dc-bet', 100)}</div><div class="res-pop" id="dc-res"></div>
      <button class="btn btn-primary btn-block btn-lg" id="dc-go" data-action="dc-go">Бросить</button></div>`);
  const DC = { dir: 'under', busy: false };
  function dcUpdate() {
    const ch = Number($('dc-chance').value), bp = Math.round(ch * 100), mult = Math.floor(970000 / bp) / 100;
    $('dc-ch').textContent = ch + '%'; $('dc-mult').textContent = x2(mult) + 'x'; $('dc-thr').textContent = (DC.dir === 'under' ? '< ' : '≥ ') + x2(DC.dir === 'under' ? ch : 100 - ch);
    const w = $('dc-win'); w.style.left = DC.dir === 'under' ? '0' : (100 - ch) + '%'; w.style.width = ch + '%';
  }
  $('dc-chance').addEventListener('input', dcUpdate); M.tabs.dice = () => dcUpdate();
  M.actions['dc-dir'] = (el) => { DC.dir = el.dataset.v; document.querySelectorAll('[data-action="dc-dir"]').forEach((b) => b.classList.toggle('active', b === el)); dcUpdate(); };
  M.actions['dc-go'] = async () => {
    if (DC.busy) return; DC.busy = true; $('dc-go').disabled = true;
    try {
      const r = await post('/api/v2/games/dice', { bet: U.bet('dc-bet'), chance: Number($('dc-chance').value), direction: DC.dir });
      $('dc-mark').style.left = r.roll + '%'; const el = $('dc-roll'); const t0 = performance.now();
      await new Promise((res) => { const f = (n) => { const t = Math.min(1, (n - t0) / 600); el.textContent = t < 1 ? x2(Math.random() * 100) : x2(r.roll); t < 1 ? requestAnimationFrame(f) : res(); }; requestAnimationFrame(f); });
      el.style.color = r.win ? 'var(--win)' : 'var(--lose)'; $('dc-res').className = 'res-pop ' + (r.win ? 'win' : 'lose'); $('dc-res').textContent = r.win ? `+${fmt(r.payout)} PTS (x${x2(r.mult)})` : 'Не повезло'; haptic(r.win ? 'success' : 'error'); U.after(r);
    } catch (e) { fail(e); } finally { DC.busy = false; $('dc-go').disabled = false; }
  };

  // ==========================================================
  //  КОЛЕСО
  // ==========================================================
  panel('wheel', head('Колесо', '50 секторов · множители до x10 · RTP 95.8%') + `
    <div class="gcard"><div class="wheel-box"><span class="wheel-pin"></span><canvas id="wh-canvas" width="600" height="600"></canvas><div class="wheel-hub" id="wh-hub">SPIN</div></div>
      <div class="legend" id="wh-legend"></div><div style="margin-top:12px">${U.betBox('wh-bet', 100)}</div><div class="res-pop" id="wh-res"></div>
      <button class="btn btn-primary btn-block btn-lg" id="wh-go" data-action="wh-go">Крутить</button></div>`);
  const WH = { rot: 0, busy: false, n: 0 };
  const whColor = (m) => ({ 0: '#2A2F4A', 1.2: '#4EA8FF', 1.5: '#2EE59D', 2: '#FFC93C', 3: '#FFA43C', 5: '#FF5D7A', 10: '#B26BFF' }[m] || '#888');
  async function whDraw() {
    if (WH.drawn) return; // колесо рисуется один раз, а не при каждом входе на вкладку
    const seg = (await config()).wheel.segments; WH.n = seg.length; WH.drawn = true; const c = $('wh-canvas'), g = c.getContext('2d'), R = 300, a = Math.PI * 2 / seg.length;
    g.clearRect(0, 0, 600, 600);
    seg.forEach((m, i) => { g.beginPath(); g.moveTo(R, R); g.arc(R, R, R - 4, -Math.PI / 2 + i * a, -Math.PI / 2 + (i + 1) * a); g.closePath(); g.fillStyle = whColor(m); g.fill(); g.strokeStyle = 'rgba(0,0,0,.35)'; g.lineWidth = 2; g.stroke();
      g.save(); g.translate(R, R); g.rotate(-Math.PI / 2 + (i + 0.5) * a); g.fillStyle = m ? '#0b0c14' : '#8B90AB'; g.font = '800 22px Manrope, sans-serif'; g.textAlign = 'right'; g.fillText(m ? m + 'x' : '·', R - 22, 8); g.restore(); });
    $('wh-legend').innerHTML = [...new Set(seg)].sort((x, y) => x - y).map((m) => `<span style="background:${whColor(m)}">${m ? m + 'x' : '0x'}</span>`).join('');
  }
  M.tabs.wheel = whDraw;
  M.actions['wh-go'] = async () => {
    if (WH.busy) return; WH.busy = true; $('wh-go').disabled = true; $('wh-res').textContent = '';
    try {
      const r = await post('/api/v2/games/wheel', { bet: U.bet('wh-bet') }); const c = $('wh-canvas'), seg = 360 / WH.n;
      c.style.transition = 'none'; c.style.transform = `rotate(${WH.rot % 360}deg)`; void c.offsetWidth;
      WH.rot = WH.rot - (WH.rot % 360) + 360 * 6 - (r.index + 0.5) * seg + (Math.random() - 0.5) * seg * 0.7;
      c.style.transition = ''; c.style.transform = `rotate(${WH.rot}deg)`; haptic('medium'); await sleep(4700);
      $('wh-hub').textContent = r.mult + 'x'; $('wh-res').className = 'res-pop ' + (r.payout > 0 ? 'win' : 'lose'); $('wh-res').textContent = r.payout > 0 ? `+${fmt(r.payout)} PTS` : 'Пусто'; haptic(r.payout > 0 ? 'success' : 'error'); U.after(r);
    } catch (e) { fail(e); } finally { WH.busy = false; $('wh-go').disabled = false; }
  };

  // ==========================================================
  //  МОНЕТКА
  // ==========================================================
  panel('coin', head('Монетка', 'Орёл или решка · выигрыш x1.94 · RTP 97%') + `
    <div class="gcard"><div class="coin-scene"><div class="coin" id="cn-coin"><div class="hd">👑</div><div class="tl">🌙</div></div></div>
      <div class="seg" style="margin:6px 0 12px"><button class="seg-btn active" data-action="cn-side" data-v="heads">👑 Орёл</button><button class="seg-btn" data-action="cn-side" data-v="tails">🌙 Решка</button></div>
      ${U.betBox('cn-bet', 100)}<div class="res-pop" id="cn-res"></div><button class="btn btn-primary btn-block btn-lg" id="cn-go" data-action="cn-go">Подбросить</button></div>`);
  const CN = { side: 'heads', rot: 0, busy: false };
  M.actions['cn-side'] = (el) => { CN.side = el.dataset.v; document.querySelectorAll('[data-action="cn-side"]').forEach((b) => b.classList.toggle('active', b === el)); };
  M.actions['cn-go'] = async () => {
    if (CN.busy) return; CN.busy = true; $('cn-go').disabled = true; $('cn-res').textContent = '';
    try {
      const r = await post('/api/v2/games/coin', { bet: U.bet('cn-bet'), side: CN.side });
      CN.rot = CN.rot - (CN.rot % 360) + 360 * 6 + (r.result === 'heads' ? 0 : 180); $('cn-coin').style.transform = `rotateY(${CN.rot}deg)`; haptic('medium'); await sleep(1750);
      $('cn-res').className = 'res-pop ' + (r.win ? 'win' : 'lose'); $('cn-res').textContent = r.win ? `Угадал! +${fmt(r.payout)} PTS` : `Выпало: ${r.result === 'heads' ? 'Орёл' : 'Решка'}`; haptic(r.win ? 'success' : 'error'); U.after(r);
    } catch (e) { fail(e); } finally { CN.busy = false; $('cn-go').disabled = false; }
  };
})();
