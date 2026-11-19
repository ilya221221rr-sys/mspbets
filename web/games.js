/* ==========================================================
   MSPBETS — игры: Краш (раунды), Мины, Plinko, Dice, Колесо, Монетка, Нарды.
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
  //  КРАШ
  // ==========================================================
  panel('crash', head('Краш', 'Общий раунд для всех · забери выигрыш до крэша') + `
    <div class="cr-hist" id="cr-hist"></div>
    <div class="cr-stage"><canvas id="cr-canvas"></canvas><div class="cr-center"><div class="cr-mult" id="cr-mult">1.00x</div><div class="cr-status" id="cr-status">Подключаемся…</div></div></div>
    <div class="gcard" style="margin-top:12px">
      ${U.betBox('cr-bet', 100)}
      <div class="row2" style="margin-top:8px"><div><label class="label" for="cr-auto">Автовывод (x)</label><input id="cr-auto" class="input" type="number" step="0.01" min="1.01" placeholder="например 2.00"></div>
      <div style="display:flex;align-items:flex-end"><span class="online" id="cr-online"></span></div></div>
      <button id="cr-btn" class="btn btn-primary btn-block btn-lg" style="margin-top:12px" data-action="cr-act" disabled>…</button>
      <div class="rtp-note">RTP 96% · шанс множителя ≥ x равен 0,96 / x</div></div>
    <div class="sub-head">Ставки в раунде <span id="cr-count" class="pill"></span></div>
    <div class="gcard cr-feed" id="cr-feed"></div>
    <div class="sub-head">Чат</div>
    <div class="gcard"><div class="chat-box" id="cr-chat"></div><form class="chat-form" id="cr-chat-form"><input class="input" id="cr-chat-in" maxlength="200" placeholder="Сообщение…" autocomplete="off"><button class="btn btn-primary btn-sm" type="submit">➤</button></form></div>
    <div class="fair" id="cr-fair"></div>`);

  const CR = { ws: null, st: null, off: 0, chat: [], lastChat: 0, raf: 0, poll: 0, retry: 0, active: false, myCash: null, prevPhase: '', hist: [], curve: [] };
  const crNow = () => Date.now() / 1000 + CR.off;
  const crMult = () => { const s = CR.st; return s && s.phase === 'running' ? Math.exp(s.growth * Math.max(0, crNow() - s.start)) : 1; };
  const crMine = () => CR.st && CR.st.bets.find((b) => b.uid === meId());

  function crConnect() {
    if (!CR.active || (CR.ws && CR.ws.readyState <= 1)) return;
    try {
      const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/crash?init=${encodeURIComponent((M.tg && M.tg.initData) || '')}`;
      const ws = new WebSocket(url); CR.ws = ws;
      ws.onmessage = (e) => { const d = JSON.parse(e.data); if (d.t === 'hello') { CR.chat = d.chat || []; CR.lastChat = CR.chat.length ? CR.chat[CR.chat.length - 1].id : 0; crChat(); } else if (d.t === 'chat') crPushChat(d.msg); else if (d.t === 'state') crApply(d); };
      ws.onopen = () => { clearInterval(CR.poll); CR.poll = 0; };
      ws.onclose = () => { if (!CR.active) return; if (!CR.poll) CR.poll = setInterval(crPoll, 1000); clearTimeout(CR.retry); CR.retry = setTimeout(crConnect, 5000); };
      ws.onerror = () => { try { ws.close(); } catch (_) {} };
    } catch (_) { if (!CR.poll) CR.poll = setInterval(crPoll, 1000); }
  }
  async function crPoll() { try { const d = await api('/api/v2/crash/state?since=' + CR.lastChat); (d.chat || []).forEach(crPushChat); crApply(d); } catch (_) {} }
  function crPushChat(m) { if (CR.chat.some((x) => x.id === m.id)) return; CR.chat.push(m); CR.chat = CR.chat.slice(-60); CR.lastChat = Math.max(CR.lastChat, m.id); crChat(); }
  function crChat() { const b = $('cr-chat'); if (!b) return; const near = b.scrollTop + b.clientHeight >= b.scrollHeight - 30; b.innerHTML = CR.chat.map((m) => `<div class="chat-msg"><b>${esc(m.name)}</b>${esc(m.text)}</div>`).join('') || '<div class="muted-note" style="border:0;background:none">Напиши первым 👋</div>'; if (near) b.scrollTop = b.scrollHeight; }

  function crApply(st) {
    CR.off = st.server_time - Date.now() / 1000;
    const prev = CR.st; CR.st = st;
    if (st.phase !== CR.prevPhase) {
      if (st.phase === 'betting') { CR.curve = []; CR.myCash = null; }
      if (st.phase === 'crashed') { const mine = st.bets.find((b) => b.uid === meId()); if (mine && !mine.cashed) haptic('error'); U.touchMe(); }
      CR.prevPhase = st.phase;
    }
    const mine = crMine();
    if (mine && mine.cashed && !CR.myCash) { CR.myCash = mine; if (!prev || !prev.bets.find((b) => b.uid === meId() && b.cashed)) { haptic('success'); M.loadMe().catch(() => {}); } }
    $('cr-online').textContent = st.online ? `● ${st.online} онлайн` : '';
    $('cr-hist').innerHTML = st.history.slice().reverse().map((h) => `<span class="${h.cp >= 10 ? 'hi' : h.cp >= 2 ? 'mid' : 'lo'}">${x2(h.cp)}x</span>`).join('');
    $('cr-count').textContent = st.bets.length;
    $('cr-feed').innerHTML = st.bets.map((b) => `<div class="cr-bet ${b.uid === meId() ? 'me' : ''} ${b.cashed ? 'won' : st.phase === 'crashed' ? 'lost' : ''}"><span class="n">${esc(b.name)}</span><span class="a">${fmt(b.amount)}</span><span class="w">${b.cashed ? `x${x2(b.cashed)} +${fmt(b.payout)}` : st.phase === 'crashed' ? '✕' : '…'}</span></div>`).join('') || '<div class="muted-note" style="border:0;background:none">Ставок пока нет — будь первым</div>';
    $('cr-fair').innerHTML = st.phase === 'crashed' && st.seed ? `Раунд #${st.round_id} · seed: ${st.seed}<br>hash до раунда: ${st.seed_hash}<br>Проверка: crash = HMAC-SHA256(seed, "mspbets-crash")` : `Раунд #${st.round_id} · хеш seed (раскроется после крэша): ${st.seed_hash}`;
    crButton();
  }
  function crButton() {
    const s = CR.st, b = $('cr-btn'); if (!s || !b) return; const mine = crMine();
    b.className = 'btn btn-block btn-lg ' + (s.phase === 'running' && mine && !mine.cashed ? 'btn-warn' : 'btn-primary');
    if (s.phase === 'betting') { b.disabled = !!mine; b.textContent = mine ? `Ставка ${fmt(mine.amount)} принята ✓` : 'Поставить'; }
    else if (s.phase === 'running') { if (mine && !mine.cashed) { b.disabled = false; b.textContent = `Забрать ${fmt(Math.floor(mine.amount * Math.floor(crMult() * 100) / 100))} PTS`; } else { b.disabled = true; b.textContent = mine ? `Забрано x${x2(mine.cashed)} ✓` : 'Ждите следующий раунд'; } }
    else { b.disabled = true; b.textContent = 'Раунд завершён'; }
  }
  function crLoop() {
    if (!CR.active) return; CR.raf = requestAnimationFrame(crLoop);
    const s = CR.st; const mEl = $('cr-mult'), stEl = $('cr-status'); if (!s || !mEl) return;
    const now = crNow(); let mode = 'live';
    if (s.phase === 'betting') { mEl.textContent = '1.00x'; mEl.className = 'cr-mult'; stEl.textContent = `Приём ставок: ${Math.max(0, s.bet_end - now).toFixed(1)} c`; mode = 'idle'; }
    else if (s.phase === 'running') {
      const m = crMult(); const mine = crMine();
      mEl.textContent = x2(m) + 'x'; mEl.className = 'cr-mult' + (mine && mine.cashed ? ' won' : ''); stEl.textContent = mine && mine.cashed ? `Вы забрали x${x2(mine.cashed)} · +${fmt(mine.payout)}` : mine ? 'Жми «Забрать», пока не поздно!' : 'Раунд идёт';
      CR.curve.push({ t: now - s.start, m }); if (CR.curve.length > 900) CR.curve.shift(); if (mine && !mine.cashed) crButton(); mode = mine && mine.cashed ? 'cashed' : 'live';
    } else { mEl.textContent = x2(s.crash_point || 1) + 'x'; mEl.className = 'cr-mult crashed'; stEl.textContent = 'Крэш!'; mode = 'crashed'; }
    crDraw(mode, s);
  }
  function crDraw(mode, s) {
    const c = $('cr-canvas'); const r = c.getBoundingClientRect(); if (!r.width) return; const dpr = Math.min(devicePixelRatio || 1, 2);
    const W = Math.round(r.width * dpr), H = Math.round(r.height * dpr); if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const g = c.getContext('2d'); g.clearRect(0, 0, W, H);
    const pad = { l: 40 * dpr, r: 14 * dpr, t: 16 * dpr, b: 18 * dpr }; const pts = CR.curve; const last = pts[pts.length - 1] || { t: 0, m: 1 };
    const endM = s.phase === 'crashed' ? (s.crash_point || last.m) : last.m;
    const tMax = Math.max(8, last.t * 1.12), mMax = Math.max(2.2, endM * 1.18); const gw = W - pad.l - pad.r, gh = H - pad.t - pad.b;
    const X = (t) => pad.l + gw * t / tMax, Y = (m) => H - pad.b - gh * (Math.log(m) / Math.log(mMax));
    g.font = `${10 * dpr}px Manrope, sans-serif`; g.textBaseline = 'middle';
    [1.5, 2, 3, 5, 10, 25, 100].filter((v) => v < mMax).forEach((v) => { g.strokeStyle = 'rgba(255,255,255,.06)'; g.lineWidth = dpr; g.beginPath(); g.moveTo(pad.l, Y(v)); g.lineTo(W - pad.r, Y(v)); g.stroke(); g.fillStyle = 'rgba(139,144,171,.85)'; g.fillText(v + 'x', 4 * dpr, Y(v)); });
    if (pts.length < 2) return;
    const col = mode === 'crashed' ? '#FF5D7A' : mode === 'cashed' ? '#2EE59D' : '#22D3EE';
    const grad = g.createLinearGradient(0, pad.t, 0, H - pad.b); grad.addColorStop(0, col + '55'); grad.addColorStop(1, col + '00');
    g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(X(p.t), Y(p.m)) : g.moveTo(X(p.t), Y(p.m)))); g.lineTo(X(last.t), H - pad.b); g.lineTo(X(0), H - pad.b); g.closePath(); g.fillStyle = grad; g.fill();
    g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(X(p.t), Y(p.m)) : g.moveTo(X(p.t), Y(p.m)))); g.strokeStyle = col; g.lineWidth = 3 * dpr; g.lineJoin = 'round'; g.stroke();
    g.beginPath(); g.arc(X(last.t), Y(last.m), 5 * dpr, 0, 7); g.fillStyle = '#fff'; g.shadowColor = col; g.shadowBlur = 14 * dpr; g.fill(); g.shadowBlur = 0;
  }
  function crStop() { CR.active = false; cancelAnimationFrame(CR.raf); clearInterval(CR.poll); CR.poll = 0; clearTimeout(CR.retry); if (CR.ws) { try { CR.ws.close(); } catch (_) {} CR.ws = null; } }
  leaveHooks.crash = crStop;
  M.tabs.crash = async () => { crStop(); CR.active = true; CR.st = null; CR.prevPhase = ''; crChat(); await crPoll(); crConnect(); crLoop(); };
  M.actions['cr-act'] = async () => {
    const s = CR.st; if (!s) return;
    try {
      if (s.phase === 'betting') { const amount = U.bet('cr-bet'); const auto = Number($('cr-auto').value) || null; const r = await post('/api/v2/crash/bet', { amount, auto }); U.after(r); haptic('medium'); }
      else if (s.phase === 'running') { const r = await post('/api/v2/crash/cashout'); ok(`Забрали x${x2(r.mult)} · +${fmt(r.payout)} PTS`); U.after(r); }
    } catch (e) { fail(e); }
  };
  $('cr-chat-form').addEventListener('submit', async (e) => { e.preventDefault(); const i = $('cr-chat-in'); const t = i.value.trim(); if (!t) return; i.value = ''; try { await post('/api/v2/crash/chat', { text: t }); } catch (er) { fail(er); } });

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
  function plDraw(ballsOn) {
    const c = $('pl-canvas'), g = c.getContext('2d'), L = plLayout(); g.clearRect(0, 0, 720, 660);
    g.fillStyle = 'rgba(255,255,255,.85)'; for (let i = 0; i < PL.rows; i++) for (let j = 0; j < i + 3; j++) { const p = L.peg(i, j); g.beginPath(); g.arc(p.x, p.y, 6, 0, 7); g.fill(); }
    (ballsOn || []).forEach((b) => { g.beginPath(); g.arc(b.x, b.y, 13, 0, 7); g.fillStyle = '#FFC93C'; g.shadowColor = '#FFC93C'; g.shadowBlur = 20; g.fill(); g.shadowBlur = 0; });
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
    const seg = (await config()).wheel.segments; WH.n = seg.length; const c = $('wh-canvas'), g = c.getContext('2d'), R = 300, a = Math.PI * 2 / seg.length;
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

  // ==========================================================
  //  НАРДЫ
  // ==========================================================
  panel('nards', head('Нарды', 'Короткие нарды против бота · победа x1.9 · кубики бросает сервер') + `
    <div class="gcard" id="nd-setup">${U.betBox('nd-bet', 100)}<button class="btn btn-primary btn-block btn-lg" style="margin-top:12px" data-action="nd-start">Начать партию</button>
      <div class="muted-note" style="margin-top:10px">Ты играешь белыми: двигай шашки с 24-го пункта к дому (правый нижний угол) и выбрасывай все 15. Бьёшь одиночные шашки соперника, он — твои. Правила: бар, удары, дубли, обязательные ходы.</div></div>
    <div id="nd-game" class="hidden"><div class="nards-info"><span id="nd-turn"></span><span id="nd-pips"></span></div>
      <div class="nb" id="nd-board"></div>
      <div class="nb-mid" id="nd-dice"></div><div class="res-pop" id="nd-res"></div>
      <div class="btn-row"><button class="btn btn-ghost hidden" id="nd-off" data-action="nd-bearoff">Выбросить шашку ▶</button><button class="btn btn-danger" id="nd-resign" data-action="nd-resign">Сдаться</button><button class="btn btn-primary hidden" id="nd-new" data-action="nd-new">Новая партия</button></div></div>`);
  const ND = { v: null, sel: null, busy: false, bet: 0 };
  const BAR = 24, OFF = -1;
  const initPos = () => { const p = Array(24).fill(0); p[23] = 2; p[12] = 5; p[7] = 3; p[5] = 5; p[0] = -2; p[11] = -5; p[16] = -3; p[18] = -5; return { pts: p, bar: [0, 0], off: [0, 0] }; };
  function localApply(st, who, mv) {
    const sg = who === 'p' ? 1 : -1, s = who === 'p' ? 0 : 1;
    if (mv.frm === BAR) st.bar[s]--; else st.pts[mv.frm] -= sg;
    if (mv.to === OFF) st.off[s]++; else { if (st.pts[mv.to] * sg === -1) { st.pts[mv.to] = 0; st.bar[1 - s]++; } st.pts[mv.to] += sg; }
  }
  const ck = (cls, n, can) => Array.from({ length: Math.min(n, 5) }, (_, k) => `<span class="ck ${cls} ${can && k === Math.min(n, 5) - 1 ? 'can' : ''}">${k === 4 && n > 5 ? n : ''}</span>`).join('');
  function ndRender(v, opts = {}) {
    const legal = opts.noLegal ? [] : (v.legal || []); const froms = new Set(legal.map((l) => l.frm)); const dests = ND.sel != null ? new Set(legal.filter((l) => l.frm === ND.sel).map((l) => l.to)) : new Set();
    const pt = (i) => { const n = v.pts[i]; const cnt = Math.abs(n); const can = n > 0 && froms.has(i) && v.bar[0] === 0;
      return `<div class="pt ${i % 2 ? 'odd' : 'even'} ${ND.sel === i ? 'sel' : ''} ${dests.has(i) ? 'dest' : ''}" data-action="nd-pt" data-i="${i}">${ck(n > 0 ? 'p' : 'b', cnt, can)}</div>`; };
    const barP = v.bar[0] ? `<div data-action="nd-pt" data-i="${BAR}" style="display:contents">${ck('p', v.bar[0], froms.has(BAR))}</div>` : '';
    $('nd-board').innerHTML = `<div class="nb-row top">${[12, 13, 14, 15, 16, 17].map(pt).join('')}<div class="nb-bar t">${ck('b', v.bar[1])}</div>${[18, 19, 20, 21, 22, 23].map(pt).join('')}</div>
      <div class="nb-row bot">${[11, 10, 9, 8, 7, 6].map(pt).join('')}<div class="nb-bar b ${ND.sel === BAR ? 'sel' : ''}">${barP}</div>${[5, 4, 3, 2, 1, 0].map(pt).join('')}</div>`;
    const dice = v.dice && v.dice.length ? v.dice : v.roll || []; const used = !(v.dice && v.dice.length);
    $('nd-dice').innerHTML = dice.map((d) => `<span class="die ${used ? 'used' : ''}">${d}</span>`).join('');
    $('nd-turn').textContent = v.winner ? 'Партия окончена' : v.turn === 'p' ? 'Ваш ход ⚪' : 'Ход бота ⚫';
    $('nd-pips').textContent = `Выброшено: вы ${v.off[0]}/15 · бот ${v.off[1]}/15 · pip ${v.pips ? v.pips[0] : '—'} / ${v.pips ? v.pips[1] : '—'}`;
    $('nd-off').classList.toggle('hidden', !(dests.has(OFF)));
  }
  async function ndPlay(events, finalView) {
    ND.busy = true; const cur = JSON.parse(JSON.stringify(ND.v || { ...initPos(), turn: 'p', dice: [], roll: [], winner: null, legal: [], bet: ND.bet, pips: null }));
    for (const e of events) {
      if (e.who === 'p' && e.move) { localApply(cur, 'p', e.move); ndRender({ ...cur, dice: [], legal: [] }, { noLegal: true }); await sleep(150); }
      else if (e.who === 'p' && e.pass) { toast(`У вас нет ходов (кубики ${e.dice.join(':')}) — пропуск`); await sleep(900); }
      else if (e.who === 'b') {
        $('nd-turn').textContent = 'Ход бота ⚫'; $('nd-dice').innerHTML = e.dice.map((d) => `<span class="die">${d}</span>`).join(''); await sleep(500);
        for (const mv of e.moves) { localApply(cur, 'b', mv); ndRender({ ...cur, dice: e.dice, legal: [] }, { noLegal: true }); if (mv.hit) haptic('warning'); await sleep(480); }
        if (e.pass) { toast('У бота нет ходов'); await sleep(500); }
      }
    }
    ND.v = finalView; ND.sel = null; ND.busy = false; ndRender(finalView); ndEndCheck(finalView);
  }
  let ndPayout = 0;
  function ndEndCheck(v) {
    const done = !!v.winner; $('nd-resign').classList.toggle('hidden', done); $('nd-new').classList.toggle('hidden', !done);
    if (done) { $('nd-res').className = 'res-pop ' + (v.winner === 'p' ? 'win' : 'lose'); $('nd-res').textContent = v.winner === 'p' ? `Победа! +${fmt(ndPayout || Math.floor(v.bet * v.win_mult))} PTS` : 'Бот выиграл партию'; haptic(v.winner === 'p' ? 'success' : 'error'); }
  }
  const ndShow = (game) => { $('nd-setup').classList.toggle('hidden', game); $('nd-game').classList.toggle('hidden', !game); };
  M.tabs.nards = async () => {
    const d = await api('/api/v2/nards/state'); $('nd-res').textContent = ''; ND.sel = null;
    if (d.active) { ND.v = d.state; ND.bet = d.state.bet; ndShow(true); ndRender(d.state); ndEndCheck(d.state); } else { ND.v = null; ndShow(false); }
  };
  M.actions['nd-start'] = async () => {
    try { const bet = U.bet('nd-bet'); const r = await post('/api/v2/nards/start', { bet }); ND.bet = bet; ND.v = null; ndPayout = 0; $('nd-res').textContent = ''; ndShow(true); U.after(r); ndRender({ ...initPos(), turn: 'p', dice: [], roll: [], winner: null, legal: [], pips: null }); await sleep(300); await ndPlay(r.events, r.state); }
    catch (e) { fail(e); }
  };
  M.actions['nd-pt'] = async (el) => {
    if (ND.busy || !ND.v || ND.v.winner || ND.v.turn !== 'p') return; const i = Number(el.dataset.i); const legal = ND.v.legal || [];
    if (ND.sel != null && legal.some((l) => l.frm === ND.sel && l.to === i)) return ndMove(ND.sel, i);
    if (legal.some((l) => l.frm === i)) { ND.sel = ND.sel === i ? null : i; haptic('light'); ndRender(ND.v); } else { ND.sel = null; ndRender(ND.v); }
  };
  M.actions['nd-bearoff'] = () => { if (ND.sel != null) ndMove(ND.sel, OFF); };
  async function ndMove(frm, to) {
    ND.busy = true;
    try { const r = await post('/api/v2/nards/move', { frm, to }); ndPayout = r.payout; if (r.balance != null) U.setBal(r.balance); if (r.unlocked && r.unlocked.length) U.popups(r.unlocked); ND.busy = false; await ndPlay(r.events, r.state); if (r.winner !== undefined || r.payout) U.touchMe(); }
    catch (e) { ND.busy = false; fail(e); ND.sel = null; ndRender(ND.v); }
  }
  M.actions['nd-resign'] = async () => { if (!(await dialog({ title: 'Сдаться?', text: 'Ставка сгорит.', okText: 'Сдаться', danger: true }))) return; try { const r = await post('/api/v2/nards/resign'); U.after(r); ND.v = { ...ND.v, winner: 'b', legal: [] }; ndRender(ND.v); ndEndCheck(ND.v); } catch (e) { fail(e); } };
  M.actions['nd-new'] = () => { ND.v = null; ND.sel = null; $('nd-res').textContent = ''; ndShow(false); };
})();
