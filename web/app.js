/* ==========================================================
   MSPBETS — клиент Telegram Mini App
   Бэкенд (FastAPI + Turso) не менялся: используются те же
   эндпоинты /api/... с тем же форматом запросов и ответов.
   ========================================================== */
(() => {
  'use strict';

  const tg = window.Telegram && window.Telegram.WebApp;
  try {
    tg && tg.ready();
    tg && tg.expand();
    const themeBg = (window.MSP_THEME && window.MSP_THEME.bg) || '#090A12'; // тема задаётся в web/theme.js
    tg && tg.setHeaderColor && tg.setHeaderColor(themeBg);
    tg && tg.setBackgroundColor && tg.setBackgroundColor(themeBg);
  } catch (_) { /* старые клиенты Telegram */ }

  const BOT_LINK = 'https://t.me/mspbetsbot';

  // ---------- Лёгкий режим для слабых телефонов ----------
  // Меньше анимаций и эффектов, если устройство слабое или включена экономия движения.
  try {
    const weak = (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) || (navigator.deviceMemory && navigator.deviceMemory <= 4) ||
      (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
    if (weak) document.documentElement.classList.add('lite');
  } catch (_) {}

  // ---------- Утилиты ----------
  const $ = (id) => document.getElementById(id);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString('ru-RU');
  const odd2 = (n) => Number(n).toFixed(2);
  const stripHtml = (s) => new DOMParser().parseFromString(String(s || ''), 'text/html').body.textContent || '';
  const show = (id) => $(id).classList.remove('hidden');
  const hide = (id) => $(id).classList.add('hidden');
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function haptic(kind) {
    try {
      const h = tg && tg.HapticFeedback;
      if (!h) return;
      if (kind === 'success' || kind === 'error' || kind === 'warning') h.notificationOccurred(kind);
      else h.impactOccurred(kind || 'light');
    } catch (_) {}
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': (tg && tg.initData) || '',
        ...(options.headers || {}),
      },
    });
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }));
    if (!res.ok) throw new Error(data.detail || 'Ошибка');
    return data;
  }
  const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

  let toastTimer = null;
  function toast(msg, kind) {
    const t = $('toast');
    t.textContent = msg;
    t.className = 'toast' + (kind ? ' ' + kind : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add('hidden'), 2800);
    if (kind === 'ok') haptic('success');
    if (kind === 'err') haptic('error');
  }
  const ok = (m) => toast(m, 'ok');
  const fail = (e) => toast(e && e.message ? e.message : String(e), 'err');

  // Собственный диалог вместо alert/confirm/prompt (работает в любом клиенте Telegram)
  function dialog({ title, text = '', fields = [], okText = 'Ок', cancelText = 'Отмена', danger = false }) {
    return new Promise((resolve) => {
      $('dialog-title').textContent = title;
      $('dialog-text').textContent = text;
      const box = $('dialog-fields');
      box.innerHTML = fields.map((f) =>
        `<label class="label">${esc(f.label)}</label>` +
        `<input class="input" data-name="${esc(f.name)}" type="${f.type || 'text'}" step="any" ` +
        `value="${esc(f.value ?? '')}" placeholder="${esc(f.placeholder || '')}">`).join('');
      const okBtn = $('dialog-ok');
      const cancelBtn = $('dialog-cancel');
      okBtn.textContent = okText;
      cancelBtn.textContent = cancelText;
      okBtn.className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary');
      show('dialog');
      const done = (v) => { hide('dialog'); okBtn.onclick = cancelBtn.onclick = null; resolve(v); };
      okBtn.onclick = () => {
        const vals = {};
        $$('input', box).forEach((i) => { vals[i.dataset.name] = i.value; });
        done(vals);
      };
      cancelBtn.onclick = () => done(null);
      const first = box.querySelector('input');
      if (first) setTimeout(() => first.focus(), 50);
    });
  }

  // ---------- Состояние ----------
  const state = {
    me: null,
    events: [],
    cases: [],
    cart: [],
    mode: 'single',
    filter: 'all',
    expressOpen: false,
    slip: null,
    bet: null,
    busy: false,
  };

  const SPORT_ICON = { 'CS2': '🎯', 'Dota 2': '🛡️', 'Футбол': '⚽', 'Теннис': '🎾', 'Баскетбол': '🏀', 'Хоккей': '🏒' };
  const fmtStart = (iso) => { try { const d = new Date(iso); const t = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }); const today = new Date(); const tm = new Date(today.getTime() + 864e5); const same = (a, b) => a.toDateString() === b.toDateString(); return (same(d, today) ? 'Сегодня' : same(d, tm) ? 'Завтра' : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })) + ', ' + t; } catch (_) { return 'Приём ставок'; } };
  // Название команд из заголовка матча: «Home — Away» (также «vs», «-», «–»)
  const teamsOf = (title) => {
    const p = String(title || '').split(/\s+(?:—|–|-|vs\.?|VS|v)\s+/);
    return p.length >= 2 ? [p[0].trim(), p.slice(1).join(' - ').trim()] : null;
  };
  // Понятная подпись исхода: «П1 — Arsenal», «Ничья», «П2 — Chelsea»
  const pickText = (title, c) => {
    const t = teamsOf(title);
    if (c === '1') return t ? `П1 — ${t[0]}` : 'П1 (победа первой команды)';
    if (c === '2') return t ? `П2 — ${t[1]}` : 'П2 (победа второй команды)';
    if (c === 'X') return 'Ничья';
    return choiceLabel(c);
  };
  const choiceLabel = (c) => (c === 'void' ? 'Возврат' : c === '1' ? 'П1' : c === 'X' ? 'Ничья' : c === '2' ? 'П2' : String(c));
  const choiceShort = (c) => (c === '1' ? 'П1' : c === 'X' ? 'X' : c === '2' ? 'П2' : String(c));
  const oddOf = (e, c) => (c === '1' ? e.odds_1 : c === 'X' ? e.odds_x : e.odds_2);

  // ---------- Профиль / баланс ----------
  let shownBalance = 0;
  function animateBalance(to) {
    const el = $('balance');
    const from = shownBalance;
    shownBalance = to;
    if (from === to || !isFinite(from)) { el.textContent = fmt(to); return; }
    const t0 = performance.now();
    const dur = 600;
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur);
      const v = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
      el.textContent = fmt(v);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  async function loadMe() {
    state.me = await api('/api/v2/me');
    animateBalance(Number(state.me.balance));
    const name = state.me.first_name || state.me.username;
    $('hero-name').textContent = name ? 'Привет, ' + name : 'MSPBETS';
    if (state.me.is_admin) $$('.admin-only').forEach((x) => x.classList.remove('hidden'));
    if (window.MSP && MSP.onMe) MSP.onMe(state.me);
  }

  // ---------- Матчи ----------
  // Игрокам сервер отдаёт только открытые матчи ближайших 24 часов; админке (all=true) — все.
  async function loadEvents(all) {
    const [evs, watch] = await Promise.all([
      api('/api/events' + (all ? '?all=1' : '')),
      api('/api/v2/watch').catch(() => null),
    ]);
    state.events = evs;
    state.watch = watch ? new Set(watch.map((w) => w.event_id)) : (state.watch || new Set());
    renderEvents();
  }
  const WINDOW_MS = 24 * 36e5;
  const inWindow = (e) => { if (!e.starts_at) return true; const t = new Date(e.starts_at).getTime(); return isNaN(t) || (t > Date.now() && t - Date.now() <= WINDOW_MS); };

  function splitTeams(title) {
    const p = String(title).split(/\s+[—–-]\s+/);
    return p.length === 2 ? p : null;
  }

  function renderEvents() {
    const open = state.events.filter((e) => e.status === 'open' && inWindow(e));
    const sports = [...new Set(open.map((e) => e.sport))];
    if (state.filter !== 'all' && !sports.includes(state.filter)) state.filter = 'all';

    $('sport-filter').innerHTML = open.length
      ? [`<button class="chip${state.filter === 'all' ? ' active' : ''}" data-action="filter" data-sport="all" type="button">Все · ${open.length}</button>`]
          .concat(sports.map((s) => `<button class="chip${state.filter === s ? ' active' : ''}" data-action="filter" data-sport="${esc(s)}" type="button">${SPORT_ICON[s] || '🏆'} ${esc(s)}</button>`))
          .join('')
      : '';

    const list = open.filter((e) => state.filter === 'all' || e.sport === state.filter);
    $('event-list').innerHTML = list.map((e) => {
      const hasDraw = !!e.odds_x;
      const teams = splitTeams(e.title);
      const teamsHtml = teams
        ? `<div class="team">${esc(teams[0])}</div><div class="vs">VS</div><div class="team">${esc(teams[1])}</div>`
        : `<div class="team team-solo">${esc(e.title)}</div>`;
      return `
        <article class="match" data-id="${e.id}">
          <div class="match-head">
            <span class="sport-badge">${SPORT_ICON[e.sport] || '🏆'} ${esc(e.league || e.sport)}</span>
            <span class="live-dot">${e.starts_at ? esc(fmtStart(e.starts_at)) : 'Приём ставок'}</span>
          </div>
          <div class="teams">${teamsHtml}</div>
          <div class="odds" style="--n:${hasDraw ? 3 : 2}">
            ${oddBtn(e, '1', 'П1', e.odds_1)}
            ${hasDraw ? oddBtn(e, 'X', 'Ничья', e.odds_x) : ''}
            ${oddBtn(e, '2', 'П2', e.odds_2)}
          </div>
          <button type="button" class="bell${state.watch && state.watch.has(e.id) ? ' on' : ''}" data-action="watch" data-id="${e.id}">${state.watch && state.watch.has(e.id) ? '🔔 Уведомим, если кэф вырастет' : '🔕 Сообщить, если кэф вырастет'}</button>
        </article>`;
    }).join('') || '<div class="empty">Сейчас нет открытых матчей.<br>Загляни чуть позже 👀</div>';
  }

  function oddBtn(e, choice, label, odd) {
    const sel = state.mode === 'express' && state.cart.some((i) => i.eventId === e.id && i.choice === choice);
    return `<button type="button" class="odd${sel ? ' selected' : ''}" data-action="odd" data-id="${e.id}" data-choice="${choice}"><span>${label}</span><b>${odd2(odd)}</b></button>`;
  }

  // ---------- Купон (одинар) ----------
  function openSlip(ev, choice) {
    state.slip = { eventId: ev.id, choice, odds: Number(oddOf(ev, choice)) };
    $('slip-event-title').textContent = ev.title;
    $('slip-event-choice').textContent = `Ваш выбор: ${pickText(ev.title, choice)}`;
    $('slip-odds').textContent = odd2(state.slip.odds);
    $('slip-amount').value = 100;
    updateSlipWin();
    show('bet-slip-modal');
  }
  function updateSlipWin() {
    if (!state.slip) return;
    const p = Number($('slip-amount').value);
    $('slip-win').textContent = p >= 1 ? `${fmt(Math.floor(p * state.slip.odds))} PTS` : '0 PTS';
  }
  async function submitSlip() {
    const s = state.slip;
    const points = Math.floor(Number($('slip-amount').value));
    if (!s || !(points >= 1)) return toast('Введите корректную сумму', 'err');
    const btn = $('slip-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Отправка...';
    try {
      await post('/api/predict', { event_id: s.eventId, points, choice: s.choice });
      ok('Ставка принята!');
      hide('bet-slip-modal');
      await loadMe();
    } catch (e) { fail(e); }
    finally { btn.disabled = false; btn.textContent = 'Сделать ставку'; }
  }

  // ---------- Экспресс ----------
  function toggleCart(ev, choice) {
    const i = state.cart.findIndex((x) => x.eventId === ev.id);
    const item = { eventId: ev.id, choice, odds: Number(oddOf(ev, choice)), title: ev.title };
    if (i !== -1 && state.cart[i].choice === choice) state.cart.splice(i, 1);
    else if (i !== -1) state.cart[i] = item;
    else state.cart.push(item);
    if (state.cart.length === 2 && !state.expressOpen) state.expressOpen = true;
    haptic('light');
    renderCart();
    renderEvents();
  }
  function totalOdds() { return state.cart.reduce((a, i) => a * i.odds, 1); }
  function renderCart() {
    const panel = $('express-panel');
    if (!state.cart.length) { panel.classList.add('hidden'); return; }
    panel.classList.remove('hidden');
    panel.classList.toggle('open', state.expressOpen);
    $('cart-count').textContent = state.cart.length;
    $('cart-total').textContent = odd2(totalOdds());
    $('cart-list').innerHTML = state.cart.map((i) => `
      <div class="cart-item">
        <span class="t">${esc(i.title)}</span>
        <span class="c">${choiceShort(i.choice)}</span>
        <span class="o">${odd2(i.odds)}</span>
        <button type="button" data-action="cart-remove" data-id="${i.eventId}" aria-label="Убрать"><svg class="ico"><use href="#i-x"/></svg></button>
      </div>`).join('');
    updateExpressWin();
  }
  function updateExpressWin() {
    const a = Number($('express-amount').value);
    $('express-win').textContent = a >= 1 ? `${fmt(Math.floor(a * totalOdds()))} PTS` : '0 PTS';
  }
  async function submitExpress() {
    if (state.cart.length < 2) return toast('Мин. 2 события для экспресса', 'err');
    const amount = Math.floor(Number($('express-amount').value));
    if (!(amount >= 1)) return toast('Введите сумму', 'err');
    try {
      const res = await post('/api/express', { amount, items: state.cart.map((i) => ({ event_id: i.eventId, choice: i.choice })) });
      ok(res.message || 'Экспресс принят!');
      state.cart = [];
      state.expressOpen = false;
      renderCart();
      renderEvents();
      await loadMe();
    } catch (e) { fail(e); }
  }

  // ---------- История и карточка ставки ----------
  const LEG_ICON = { won: '✅', lost: '❌', void: '↩️', pending: '⏳' };
  const LEG_TXT = { won: 'Зашло', lost: 'Не зашло', void: 'Возврат', pending: 'Ожидает' };
  const potentialOf = (h) => Math.floor(h.points * h.locked_odds);
  const histClass = (h) => (h.status === 'won' ? 'won' : h.status === 'lost' ? 'lost' : h.status === 'cashed' ? 'cashed' : 'pending');

  function legsSummary(h) {
    if (h.kind !== 'express') return '';
    const won = h.legs.filter((l) => l.status === 'won').length;
    return ` · Зашло ${won} из ${h.legs.length}`;
  }

  async function loadHistory() {
    $('history-list').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
    try {
      const hist = await api('/api/history');
      $('history-list').innerHTML = hist.length ? hist.map((h, idx) => {
        const st = h.status === 'won' ? `Выиграл: +${fmt(potentialOf(h))} PTS`
          : h.status === 'lost' ? 'Проиграл' : h.status === 'void' ? 'Возврат ставки'
          : h.status === 'cashed' ? `Выкуплена: +${fmt(h.cashout || 0)} PTS` : 'В ожидании';
        const tag = h.cashout_offer ? `<span class="hist-tag">💸 Выкуп ${fmt(h.cashout_offer)}</span>` : '';
        return `
          <button type="button" class="hist ${histClass(h)}" data-action="bet-open" data-idx="${idx}">
            <div class="hist-top"><span class="hist-sport">${esc(h.sport)}</span><span class="hist-meta">Кф ${odd2(h.locked_odds)}</span></div>
            <div class="hist-title">${esc(h.title)}</div>
            <div class="hist-meta">${h.kind === 'express' ? 'Экспресс' : 'Исход: ' + esc(pickText(h.title, h.choice))} · Ставка: ${fmt(h.points)} PTS${legsSummary(h)}</div>
            <div class="hist-status">${st}</div>${tag}
          </button>`;
      }).join('') : '<div class="empty">История пуста.<br>Сделай первую ставку!</div>';
      state.history = hist;
    } catch (e) { $('history-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  function legsHtml(h) {
    const single = h.kind !== 'express';
    const head = single ? 'Итог матча' : 'Исходы событий';
    return `<div class="bet-legs-head"><span>${head}</span><span>${legsSummary(h).replace(' · ', '')}</span></div>` + h.legs.map((l) => `
      <div class="leg ${l.status}">
        <span class="leg-ic">${LEG_ICON[l.status] || '⏳'}</span>
        <div class="leg-main">
          ${single ? '' : `<div class="leg-title">${esc(l.title)}</div>`}
          <div class="leg-meta">Твой исход: <b>${esc(pickText(l.title, l.choice))}</b> · кф ${odd2(l.odds)}${l.result && l.status !== 'pending' ? ` · Итог: <b>${esc(choiceLabel(l.result))}</b>` : ''}</div>
        </div>
        <span class="leg-st">${LEG_TXT[l.status] || ''}</span>
      </div>`).join('');
  }

  function setCashoutBtn(h, offer) {
    const btn = $('bet-cashout-btn');
    if (h) h.cashout_offer = offer || null;
    if (offer) { btn.textContent = `💸 Выкупить за ${fmt(offer)} PTS`; btn.classList.remove('hidden'); }
    else btn.classList.add('hidden');
  }

  function openBetCard(h) {
    state.bet = h;
    $('bet-card-title').textContent = h.title;
    $('bet-card-sport').textContent = h.sport;
    $('bet-card-choice').textContent = h.kind === 'express' ? 'Экспресс' : pickText(h.title, h.choice);
    $('bet-card-points').textContent = `${fmt(h.points)} PTS`;
    $('bet-card-odds').textContent = odd2(h.locked_odds);
    const label = $('bet-card-win-label'), win = $('bet-card-win');
    if (h.status === 'cashed') { label.textContent = 'Выкуп'; win.textContent = `${fmt(h.cashout || 0)} PTS`; }
    else if (h.status === 'lost') { label.textContent = 'Выигрыш'; win.textContent = '0 PTS'; }
    else if (h.status === 'void') { label.textContent = 'Возврат'; win.textContent = `${fmt(h.points)} PTS`; }
    else { label.textContent = h.status === 'won' ? 'Выигрыш' : 'Возможный выигрыш'; win.textContent = `${fmt(potentialOf(h))} PTS`; }
    const banner = $('bet-card-banner');
    banner.className = 'bet-banner' + (h.status === 'won' ? ' won' : h.status === 'lost' ? ' lost' : h.status === 'cashed' ? ' cashed' : '');
    $('bet-card-status').textContent = { won: 'Выигрыш', lost: 'Проигрыш', cashed: 'Выкуплена', void: 'Возврат' }[h.status] || 'В ожидании';
    $('bet-card-legs').innerHTML = legsHtml(h);
    setCashoutBtn(h, h.status === 'pending' ? h.cashout_offer : null);
    show('bet-modal');
    if (h.status === 'pending' && h.cashout_offer) {  // подтягиваем свежую цену выкупа
      api(`/api/cashout/offer?kind=${h.kind}&id=${h.id}`).then((r) => { if (state.bet === h) setCashoutBtn(h, r.offer); }).catch(() => {});
    }
  }

  async function cashoutBet() {
    const h = state.bet;
    if (!h || !h.cashout_offer || state.busy) return;
    const ok_ = await dialog({ title: 'Выкупить ставку?', text: `Ставка ${fmt(h.points)} PTS будет закрыта сейчас, вы получите ${fmt(h.cashout_offer)} PTS. Дождаться результата уже не получится.`, okText: 'Выкупить' });
    if (!ok_) return;
    state.busy = true;
    try {
      const r = await post('/api/cashout', { kind: h.kind, id: h.id, expected: h.cashout_offer });
      ok(r.message || 'Ставка выкуплена');
      hide('bet-modal');
      await loadMe();
      await loadHistory();
    } catch (e) {
      fail(e);
      try { const r = await api(`/api/cashout/offer?kind=${h.kind}&id=${h.id}`); setCashoutBtn(h, r.offer); } catch (_) {}
    } finally { state.busy = false; }
  }

  function shareBet() {
    const b = state.bet;
    if (!b) return;
    let text;
    if (b.kind === 'express') {
      const legs = (b.legs || []).map((l, i) => `${i + 1}. ${l.title}\n    ✅ Исход: ${pickText(l.title, l.choice)} · кф ${odd2(l.odds)}`).join('\n');
      text = `🔥 Мой экспресс в MSPBETS!\n\n${legs}\n\n💰 Ставка: ${b.points} PTS\n📈 Общий кф: ${odd2(b.locked_odds)}\n\n👉 Заходи и прогнозируй: ${BOT_LINK}`;
    } else {
      text = `🔥 Моя ставка в MSPBETS!\n\n🏆 ${b.sport}\n⚔️ ${b.title}\n✅ Мой исход: ${pickText(b.title, b.choice)}\n💰 Ставка: ${b.points} PTS\n📈 Кф: ${odd2(b.locked_odds)}\n\n👉 Заходи и прогнозируй: ${BOT_LINK}`;
    }
    try {
      if (tg && tg.openTelegramLink) {
        tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(BOT_LINK)}&text=${encodeURIComponent(text)}`);
        return;
      }
    } catch (_) {}
    try { navigator.clipboard.writeText(text); ok('Текст скопирован в буфер обмена!'); }
    catch (_) { toast('Не удалось поделиться', 'err'); }
  }

  // ---------- Рейтинг ----------
  async function loadRating() {
    $('rating-list').innerHTML = '<div class="skeleton"></div>';
    try {
      const rows = await api('/api/rating');
      if (!rows.length) { $('rating-list').innerHTML = '<div class="empty">Пока никого нет</div>'; return; }
      const me = state.me || {};
      const isMe = (r) => (r.username && r.username === me.username) && (r.first_name || '') === (me.first_name || '');
      const nm = (r) => esc(r.first_name || r.username || 'Игрок');
      const medals = ['🥇', '🥈', '🥉'];
      const top = rows.slice(0, 3);
      const order = top.length === 3 ? [1, 0, 2] : top.map((_, i) => i);
      const podium = `<div class="podium">${order.map((i) => `
        <div class="pod p${i + 1}" data-action="player" data-uid="${top[i].user_id}"><div class="medal">${medals[i]}</div>${MSP.U.ava(nm(top[i]), top[i].frame, 'sm', top[i].avatar)}<div class="nm">${nm(top[i])}</div><div class="sc">${fmt(top[i].balance)}</div></div>`).join('')}</div>`;
      const rest = rows.slice(3).map((r, i) => `
        <div class="rank${isMe(r) ? ' me' : ''}" data-action="player" data-uid="${r.user_id}"><span class="pos">${i + 4}</span>${MSP.U.ava(nm(r), r.frame, 'sm', r.avatar)}<span class="nm">${nm(r)}</span><span class="sc">${fmt(r.balance)} PTS</span></div>`).join('');
      $('rating-list').innerHTML = podium + rest;
    } catch (e) { $('rating-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  // ---------- Новости ----------
  async function loadNews() {
    const el = $('news-list');
    el.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
    try {
      const news = await api('/api/dota-news');
      if (!news || !news.length) { el.innerHTML = '<div class="empty">Не удалось загрузить новости.</div>'; return; }
      state.news = news;
      el.innerHTML = news.map((n, i) => `
        <button type="button" class="news-card" data-action="news-open" data-idx="${i}">
          <div class="news-title">${esc(stripHtml(n.title))}</div>
          <div class="news-date">${esc(n.published)}</div>
          <div class="news-sum">${esc(stripHtml(n.summary))}</div>
        </button>`).join('');
    } catch (e) { el.innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`; }
  }
  function openLink(url) {
    if (!/^https?:\/\//i.test(url || '')) return;
    if (tg && tg.openLink) tg.openLink(url); else window.open(url, '_blank', 'noopener');
  }

  // Чтение новости прямо в приложении (текст и картинки подгружает сервер, без перехода на сайт)
  const fmtNewsDate = (v) => { try { const d = new Date(v); return isNaN(d) ? String(v || '') : d.toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }); } catch (_) { return String(v || ''); } };
  let articleToken = 0;
  async function openArticle(n) {
    if (!n) return;
    const token = ++articleToken;
    const box = $('article-body');
    const head = `<h1 class="article-title">${esc(stripHtml(n.title))}</h1><div class="news-date">${esc(fmtNewsDate(n.published))}</div>`;
    const site = `<button type="button" class="btn btn-ghost article-site" data-action="news-site" data-link="${esc(n.link)}">Открыть на сайте</button>`;
    box.innerHTML = head + '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
    await showTab('news-article');
    try {
      const d = await api('/api/news-detail?url=' + encodeURIComponent(n.link));
      if (token !== articleToken) return;
      box.innerHTML = `<h1 class="article-title">${esc(d.title || stripHtml(n.title))}</h1><div class="news-date">${esc(fmtNewsDate(n.published))}</div>` +
        (d.image ? `<img class="article-cover" src="${esc(d.image)}" alt="" referrerpolicy="no-referrer">` : '') +
        `<div class="article-text">${d.html}</div>` + site;
    } catch (e) {
      if (token !== articleToken) return;
      box.innerHTML = head + `<p class="article-text">${esc(stripHtml(n.summary))}</p><div class="empty">Полный текст сейчас не загрузился: ${esc(e.message)}</div>` + site;
    }
  }

  // ---------- «Что нового»: сообщения админа при заходе ----------
  async function checkUpdates() {
    let list;
    try { list = await api('/api/v2/updates/unseen'); } catch (_) { return; }
    if (!list || !list.length) return;
    const ids = list.map((u) => u.id);
    for (let i = 0; i < list.length; i++) {
      const u = list[i];
      $('upd-title').textContent = u.title;
      $('upd-body').textContent = u.body;
      $('upd-date').textContent = fmtNewsDate(u.created_at);
      $('upd-count').textContent = list.length > 1 ? `${i + 1}/${list.length}` : '';
      $('upd-ok').textContent = i < list.length - 1 ? 'Дальше' : 'Понятно';
      show('update-modal'); haptic('success');
      await new Promise((r) => { $('upd-ok').onclick = r; });
      hide('update-modal');
    }
    try { await post('/api/v2/updates/seen', { ids }); } catch (_) {}
  }

  async function loadAdminUpdates() {
    const box = $('updates-admin-list');
    if (!box) return;
    try {
      const r = await api('/api/v2/admin/updates');
      state.adminUpdates = r.items;
      box.innerHTML = r.items.length ? r.items.map((u) => `
        <div class="promo-row">
          <div class="promo-info"><div class="promo-code">${esc(u.title)}</div><div class="promo-meta">${esc(fmtNewsDate(u.created_at))} · прочитали: ${fmt(u.seen)} из ${fmt(r.total_users)}</div></div>
          <button type="button" class="btn btn-danger btn-sm" data-action="upd-delete" data-id="${u.id}" aria-label="Удалить">🗑</button>
        </div>`).join('') : '<div class="empty">Постов пока нет</div>';
    } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  async function deleteUpdate(id) {
    const u = (state.adminUpdates || []).find((x) => x.id === id);
    const v = await dialog({ title: 'Удалить пост?', text: `«${u ? u.title : id}» пропадёт у тех, кто ещё не успел его прочитать.`, okText: 'Удалить', danger: true });
    if (!v) return;
    try { const r = await post('/api/v2/admin/updates/delete', { id }); ok(r.message || 'Удалено'); await loadAdminUpdates(); } catch (e) { fail(e); }
  }

  // Краш, кейсы, инвентарь и апгрейд вынесены в /web/games.js и /web/extra.js (новые честные версии)

  // ---------- Промокод ----------
  async function activatePromo() {
    const res = await dialog({ title: 'Промокод', text: 'Введите код и получите баллы', fields: [{ name: 'code', label: 'Код', placeholder: 'WELCOME' }], okText: 'Активировать' });
    if (!res || !res.code.trim()) return;
    try { const r = await post('/api/promo/activate', { code: res.code.trim() }); ok(r.message || 'Промокод активирован'); await loadMe(); }
    catch (e) { fail(e); }
  }

  // ---------- Админка ----------
  async function loadAdminUsers() {
    if (!state.me || !state.me.is_admin) return;
    try {
      const users = await api('/api/admin/users');
      const opts = '<option value="" disabled selected>Выберите игрока...</option>' +
        users.map((u) => `<option value="${esc(u.user_id)}">${esc(u.first_name || 'Без имени')} (@${esc(u.username || u.user_id)})</option>`).join('');
      $('drop-user-id').innerHTML = opts;
      if ($('settings-user-id')) $('settings-user-id').innerHTML = opts;
    } catch (_) {}
  }

  async function loadBetsReport() {
    const box = $('bets-report-list');
    box.innerHTML = '<div class="empty">Загрузка...</div>';
    try {
      const report = await api('/api/admin/bets-report');
      if (!report || !report.length) { box.innerHTML = '<div class="empty">Ставок пока нет.</div>'; return; }
      box.innerHTML = report.map((r) => `
        <div class="rep ${r.status === 'won' ? 'won' : r.status === 'lost' ? 'lost' : r.status === 'cashed' ? 'cashed' : ''}">
          <div class="rep-top"><span>${esc(r.user)} <small>(${esc(r.type)})</small></span><span>${fmt(r.points)} PTS · кф ${esc(r.odds)}</span></div>
          <div class="rep-ev">${esc(r.event)}</div>
          <div class="rep-st">${{ pending: 'В ожидании', won: 'Выиграл', lost: 'Проиграл', cashed: 'Выкуплена', void: 'Возврат' }[r.status] || esc(r.status)}</div>
        </div>`).join('');
    } catch (e) { box.innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`; }
  }

  function loadSettle() {
    const list = state.events.filter((e) => e.status === 'open' || e.status === 'closed');
    $('settle-list').innerHTML = list.map((e) => `
      <div class="settle">
        <div class="settle-top"><span>${esc(e.title)}</span><span class="${e.status === 'open' ? 'st-open' : 'st-closed'}">${e.status === 'open' ? 'ОТКРЫТ' : 'ЗАКРЫТ'}</span></div>
        <div class="settle-odds">Кф: ${esc(e.odds_1)} / ${esc(e.odds_x || '-')} / ${esc(e.odds_2)}</div>
        <div class="settle-row">
          <button type="button" class="btn btn-warn btn-sm" data-action="odds-edit" data-id="${e.id}">✏️ Кф</button>
          <button type="button" class="btn ${e.status === 'open' ? 'btn-danger' : 'btn-success'} btn-sm" data-action="toggle" data-id="${e.id}">${e.status === 'open' ? '🔒 Закрыть' : '🔓 Открыть'}</button>
        </div>
        <div class="settle-row">
          <button type="button" class="btn btn-ghost btn-sm" data-action="settle" data-id="${e.id}" data-result="1">П1</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="settle" data-id="${e.id}" data-result="X">X</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="settle" data-id="${e.id}" data-result="2">П2</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="settle" data-id="${e.id}" data-result="void">Возврат</button>
        </div>
      </div>`).join('') || '<div class="empty">Нет матчей</div>';
  }

  async function refreshAdminEvents() { await loadEvents(true); loadSettle(); }

  async function toggleEvent(id) {
    try { const r = await post(`/api/admin/toggle-event/${id}`); ok(r.message || 'Готово'); await refreshAdminEvents(); } catch (e) { fail(e); }
  }
  async function editOdds(id) {
    const e = state.events.find((x) => x.id === id);
    if (!e) return;
    const v = await dialog({
      title: 'Изменить коэффициенты', text: e.title, okText: 'Сохранить',
      fields: [
        { name: 'o1', label: 'П1', value: e.odds_1, type: 'number' },
        { name: 'ox', label: 'X (пусто, если нет)', value: e.odds_x || '', type: 'number' },
        { name: 'o2', label: 'П2', value: e.odds_2, type: 'number' },
      ],
    });
    if (!v) return;
    try {
      await post(`/api/admin/update-odds/${id}`, { o1: Number(v.o1), ox: v.ox ? Number(v.ox) : null, o2: Number(v.o2) });
      ok('Кэфы обновлены!');
      await refreshAdminEvents();
    } catch (err) { fail(err); }
  }
  async function settleEvent(id, result) {
    const e = state.events.find((x) => x.id === id);
    const v = await dialog({ title: 'Рассчитать матч?', text: `${e ? e.title : ''}\nПобедитель: ${choiceLabel(result)}`, okText: 'Рассчитать', danger: true });
    if (!v) return;
    try { await post(`/api/admin/settle/${id}`, { result }); ok('Рассчитано!'); await refreshAdminEvents(); } catch (err) { fail(err); }
  }

  function bindForm(id, handler) {
    const f = $(id);
    if (f) f.addEventListener('submit', async (ev) => { ev.preventDefault(); try { await handler(f); } catch (e) { fail(e); } });
  }
  bindForm('admin-points-form', async (f) => {
    const r = await post('/api/admin/give-points', { target: $('admin-target').value, amount: Number($('admin-points-amount').value), action: $('admin-points-action').value });
    ok(r.message); f.reset(); await loadMe();
  });
  bindForm('admin-dropdown-points', async () => {
    const r = await post('/api/admin/give-points', { target: $('drop-user-id').value, amount: Number($('drop-points-amount').value), action: $('drop-points-action').value });
    ok(r.message); await loadAdminUsers(); await loadMe();
  });
  bindForm('admin-broadcast-form', async (f) => {
    const text = $('broadcast-text').value.trim();
    if (!text) return toast('Введите текст', 'err');
    const v = await dialog({ title: 'Отправить всем игрокам?', text: text.length > 140 ? text.slice(0, 140) + '…' : text, okText: 'Отправить', danger: true });
    if (!v) return;
    const r = await post('/api/admin/broadcast', { text });
    ok(r.message); f.reset();
  });
  bindForm('admin-event-form', async (f) => {
    await post('/api/admin/create-event', {
      sport: $('admin-sport').value, title: $('admin-title').value,
      o1: Number($('admin-o1').value), ox: $('admin-ox').value ? Number($('admin-ox').value) : null, o2: Number($('admin-o2').value),
    });
    ok('Матч добавлен! Уведомления уйдут через 60 секунд.'); f.reset(); await refreshAdminEvents();
  });
  async function loadPromos() {
    const box = $('promo-list');
    if (!box) return;
    try {
      const list = await api('/api/admin/promos');
      state.promos = list;
      box.innerHTML = list.length ? list.map((p, i) => `
        <div class="promo-row">
          <div class="promo-info"><div class="promo-code">${esc(p.code)}</div><div class="promo-meta">+${fmt(p.reward)} PTS · активаций: ${fmt(p.uses)}</div></div>
          <button type="button" class="btn btn-warn btn-sm" data-action="promo-edit" data-idx="${i}" aria-label="Изменить">✏️</button>
          <button type="button" class="btn btn-danger btn-sm" data-action="promo-delete" data-idx="${i}" aria-label="Удалить">🗑</button>
        </div>`).join('') : '<div class="empty">Промокодов пока нет</div>';
    } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  async function editPromo(p) {
    const v = await dialog({
      title: 'Изменить промокод', text: `Сейчас: ${p.code} · +${fmt(p.reward)} PTS`, okText: 'Сохранить',
      fields: [
        { name: 'code', label: 'Код', value: p.code },
        { name: 'reward', label: 'Награда (PTS)', value: p.reward, type: 'number' },
      ],
    });
    if (!v) return;
    try {
      const r = await post('/api/admin/update-promo', { code: p.code, new_code: v.code, reward: Number(v.reward) });
      ok(r.message || 'Сохранено'); await loadPromos();
    } catch (e) { fail(e); }
  }
  async function deletePromo(p) {
    const v = await dialog({ title: 'Удалить промокод?', text: `${p.code} (+${fmt(p.reward)} PTS, активаций: ${fmt(p.uses)}) будет удалён безвозвратно.`, okText: 'Удалить', danger: true });
    if (!v) return;
    try {
      const r = await post('/api/admin/delete-promo', { code: p.code });
      ok(r.message || 'Удалено'); await loadPromos();
    } catch (e) { fail(e); }
  }
  bindForm('admin-update-form', async (f) => {
    const r = await post('/api/v2/admin/updates', { title: $('update-title').value, body: $('update-text').value });
    ok(r.message || 'Опубликовано'); f.reset(); await loadAdminUpdates();
  });
  bindForm('admin-promo-form', async (f) => {
    await post('/api/admin/create-promo', { code: $('admin-promo-code').value, reward: Number($('admin-promo-reward').value) });
    ok('Промокод создан!'); f.reset(); await loadPromos();
  });

  // ---------- Навигация ----------
  async function showTab(name) {
    $$('.panel').forEach((p) => p.classList.toggle('hidden', p.id !== name));
    const PARENT = { slots: 'games', crash: 'games', mines: 'games', plinko: 'games', dice: 'games', wheel: 'games', coin: 'games', ledger: 'profile', achievements: 'profile', referral: 'profile', social: 'profile', player: 'profile', history: 'profile', news: 'profile', 'news-article': 'profile', rating: 'profile' };
    $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === (PARENT[name] || name)));
    if (window.MSP && MSP.leave) MSP.leave(name);
    window.scrollTo({ top: 0 });
    try {
      if (name === 'matches') await loadEvents();
      if (name === 'history') await loadHistory();
      if (name === 'rating') await loadRating();
      if (name === 'news') await loadNews();
      if (window.MSP && MSP.tabs[name]) await MSP.tabs[name]();
      if (name === 'admin') { await Promise.all([loadAdminUsers(), loadEvents(true)]); loadSettle(); loadBetsReport(); loadPromos(); loadAdminUpdates(); }
    } catch (e) { fail(e); }
  }

  // ---------- Делегирование кликов ----------
  const actions = {
    mode(el) {
      state.mode = el.dataset.mode;
      $$('.seg-btn').forEach((b) => b.classList.toggle('active', b === el));
      if (state.mode === 'single') { state.cart = []; renderCart(); }
      renderEvents();
    },
    filter(el) { state.filter = el.dataset.sport; renderEvents(); },
    odd(el) {
      const ev = state.events.find((e) => e.id === Number(el.dataset.id));
      if (!ev) return;
      haptic('light');
      if (state.mode === 'express') toggleCart(ev, el.dataset.choice);
      else openSlip(ev, el.dataset.choice);
    },
    'slip-close'() { hide('bet-slip-modal'); },
    'slip-backdrop'(el, e) { if (e.target === el) hide('bet-slip-modal'); },
    'slip-chip'(el) {
      $('slip-amount').value = el.dataset.amount === 'max' ? Math.max(1, Math.floor((state.me && state.me.balance) || 1)) : el.dataset.amount;
      updateSlipWin();
    },
    'slip-submit': submitSlip,
    'express-toggle'() { state.expressOpen = !state.expressOpen; renderCart(); },
    'express-clear'() { state.cart = []; state.expressOpen = false; renderCart(); renderEvents(); },
    'express-submit': submitExpress,
    'cart-remove'(el) { state.cart = state.cart.filter((i) => i.eventId !== Number(el.dataset.id)); renderCart(); renderEvents(); },
    'bet-open'(el) { const h = (state.history || [])[Number(el.dataset.idx)]; if (h) openBetCard(h); },
    'bet-close'() { hide('bet-modal'); },
    'bet-cashout': cashoutBet,
    'promo-edit'(el) { const p = (state.promos || [])[Number(el.dataset.idx)]; if (p) editPromo(p); },
    'promo-delete'(el) { const p = (state.promos || [])[Number(el.dataset.idx)]; if (p) deletePromo(p); },
    'bet-backdrop'(el, e) { if (e.target === el) hide('bet-modal'); },
    share: shareBet,
    'news-open'(el) { openArticle((state.news || [])[Number(el.dataset.idx)]); },
    'news-site'(el) { openLink(el.dataset.link); },
    'upd-delete'(el) { deleteUpdate(Number(el.dataset.id)); },
    'bets-report': loadBetsReport,
    'odds-edit'(el) { editOdds(Number(el.dataset.id)); },
    toggle(el) { toggleEvent(Number(el.dataset.id)); },
    settle(el) { settleEvent(Number(el.dataset.id), el.dataset.result); },
  };

  document.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-tab]');
    if (tab) { showTab(tab.dataset.tab); return; }
    const el = e.target.closest('[data-action]');
    if (el && actions[el.dataset.action]) actions[el.dataset.action](el, e);
    else if (el && window.MSP && MSP.actions[el.dataset.action]) MSP.actions[el.dataset.action](el, e);
  });

  $('slip-amount').addEventListener('input', updateSlipWin);
  $('express-amount').addEventListener('input', updateExpressWin);
  $('btn-promo').addEventListener('click', activatePromo);

  // ---------- Мост для новых модулей (extra.js, games.js) ----------
  window.MSP = {
    tg, api, post, toast, ok, fail, dialog, fmt, esc, haptic, sleep, state, showTab, loadMe, animateBalance, openLink, pickText,
    renderEvents, loadEvents, tabs: {}, actions: {},
    watchToggle: async (id) => {
      const ev = state.events.find((e) => e.id === id); if (!ev) return;
      const on = !(state.watch && state.watch.has(id));
      const choices = ['1'].concat(ev.odds_x ? ['X'] : [], ['2']);
      try { for (const c of choices) await post('/api/v2/watch', { event_id: id, choice: c, on }); on ? state.watch.add(id) : state.watch.delete(id); ok(on ? 'Подписка включена: напишем в бота, если кэф вырастет' : 'Подписка отключена'); renderEvents(); } catch (e) { fail(e); }
    },
  };

  // ---------- Старт ----------
  (async () => {
    $('event-list').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
    try { await loadMe(); await loadEvents(); }
    catch (e) { $('event-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
    checkUpdates();
  })();
})();
