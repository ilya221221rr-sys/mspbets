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
    tg && tg.setHeaderColor && tg.setHeaderColor('#090A12');
    tg && tg.setBackgroundColor && tg.setBackgroundColor('#090A12');
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
    $('slip-event-choice').textContent = `Ваш выбор: ${choiceLabel(choice)}`;
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
  async function loadHistory() {
    $('history-list').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
    try {
      const hist = await api('/api/history');
      $('history-list').innerHTML = hist.length ? hist.map((h, idx) => {
        const cls = h.status === 'won' ? 'won' : h.status === 'lost' ? 'lost' : 'pending';
        const st = h.status === 'won' ? `Выиграл: +${fmt(Math.floor(h.points * h.locked_odds))} PTS`
          : h.status === 'lost' ? 'Проиграл' : h.status === 'void' ? 'Возврат ставки' : 'В ожидании';
        return `
          <button type="button" class="hist ${cls}" data-action="bet-open" data-idx="${idx}">
            <div class="hist-top"><span class="hist-sport">${esc(h.sport)}</span><span class="hist-meta">Кф ${odd2(h.locked_odds)}</span></div>
            <div class="hist-title">${esc(h.title)}</div>
            <div class="hist-meta">Исход: ${esc(choiceLabel(h.choice))} · Ставка: ${fmt(h.points)} PTS</div>
            <div class="hist-status">${st}</div>
          </button>`;
      }).join('') : '<div class="empty">История пуста.<br>Сделай первую ставку!</div>';
      state.history = hist;
    } catch (e) { $('history-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  function openBetCard(h) {
    state.bet = h;
    $('bet-card-title').textContent = h.title;
    $('bet-card-sport').textContent = h.sport;
    $('bet-card-choice').textContent = choiceLabel(h.choice);
    $('bet-card-points').textContent = `${fmt(h.points)} PTS`;
    $('bet-card-odds').textContent = odd2(h.locked_odds);
    $('bet-card-win').textContent = `${fmt(Math.floor(h.points * h.locked_odds))} PTS`;
    const banner = $('bet-card-banner');
    banner.className = 'bet-banner' + (h.status === 'won' ? ' won' : h.status === 'lost' ? ' lost' : '');
    $('bet-card-status').textContent = h.status === 'won' ? 'Выигрыш' : h.status === 'lost' ? 'Проигрыш' : 'В ожидании';
    show('bet-modal');
  }

  function shareBet() {
    const b = state.bet;
    if (!b) return;
    const text = `🔥 Моя ставка в MSPBETS!\n\n🏆 ${b.sport}\n⚔️ ${b.title}\n💰 Ставка: ${b.points} PTS на ${choiceLabel(b.choice)}\n📈 Кф: ${odd2(b.locked_odds)}\n\n👉 Заходи и прогнозируй: ${BOT_LINK}`;
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
        <div class="pod p${i + 1}" data-action="player" data-uid="${top[i].user_id}"><div class="medal">${medals[i]}</div><div class="nm">${nm(top[i])}</div><div class="sc">${fmt(top[i].balance)}</div></div>`).join('')}</div>`;
      const rest = rows.slice(3).map((r, i) => `
        <div class="rank${isMe(r) ? ' me' : ''}" data-action="player" data-uid="${r.user_id}"><span class="pos">${i + 4}</span><span class="nm">${nm(r)}</span><span class="sc">${fmt(r.balance)} PTS</span></div>`).join('');
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
      $('settings-user-id').innerHTML = opts;
    } catch (_) {}
  }

  async function loadBetsReport() {
    const box = $('bets-report-list');
    box.innerHTML = '<div class="empty">Загрузка...</div>';
    try {
      const report = await api('/api/admin/bets-report');
      if (!report || !report.length) { box.innerHTML = '<div class="empty">Ставок пока нет.</div>'; return; }
      box.innerHTML = report.map((r) => `
        <div class="rep ${r.status === 'won' ? 'won' : r.status === 'lost' ? 'lost' : ''}">
          <div class="rep-top"><span>${esc(r.user)} <small>(${esc(r.type)})</small></span><span>${fmt(r.points)} PTS · кф ${esc(r.odds)}</span></div>
          <div class="rep-ev">${esc(r.event)}</div>
          <div class="rep-st">${r.status === 'pending' ? 'В ожидании' : r.status === 'won' ? 'Выиграл' : 'Проиграл'}</div>
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
    ok(r.message); f.reset();
  });
  bindForm('admin-dropdown-points', async () => {
    const r = await post('/api/admin/give-points', { target: $('drop-user-id').value, amount: Number($('drop-points-amount').value), action: $('drop-points-action').value });
    ok(r.message); await loadAdminUsers();
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
  bindForm('admin-promo-form', async (f) => {
    await post('/api/admin/create-promo', { code: $('admin-promo-code').value, reward: Number($('admin-promo-reward').value) });
    ok('Промокод создан!'); f.reset();
  });

  // ---------- Навигация ----------
  async function showTab(name) {
    $$('.panel').forEach((p) => p.classList.toggle('hidden', p.id !== name));
    const PARENT = { crash: 'games', mines: 'games', plinko: 'games', dice: 'games', wheel: 'games', coin: 'games', nards: 'games', ledger: 'profile', achievements: 'profile', referral: 'profile', social: 'profile', player: 'profile', history: 'profile', news: 'profile', rating: 'profile' };
    $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === (PARENT[name] || name)));
    if (window.MSP && MSP.leave) MSP.leave(name);
    window.scrollTo({ top: 0 });
    try {
      if (name === 'matches') await loadEvents();
      if (name === 'history') await loadHistory();
      if (name === 'rating') await loadRating();
      if (name === 'news') await loadNews();
      if (window.MSP && MSP.tabs[name]) await MSP.tabs[name]();
      if (name === 'admin') { await Promise.all([loadAdminUsers(), loadEvents(true)]); loadSettle(); loadBetsReport(); }
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
    'bet-backdrop'(el, e) { if (e.target === el) hide('bet-modal'); },
    share: shareBet,
    'news-open'(el) { const n = (state.news || [])[Number(el.dataset.idx)]; if (n) openLink(n.link); },
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
    tg, api, post, toast, ok, fail, dialog, fmt, esc, haptic, sleep, state, showTab, loadMe, animateBalance, openLink,
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
  })();
})();
