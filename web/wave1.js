/* ==========================================================
   MSPBETS — первая волна MSP 3.0:
     • 🔥 бонус к экспрессу (+5% … +50%) и 🛡 страховка экспресса — прямо в купонах
     • 🎁 фрибеты: кнопка «Фрибетом» в купоне П1/X/П2, в окне рынков и в экспрессе+
     • 🎟 сезонный пропуск: 30 уровней за ставки месяца, бесплатная и премиум-дорожки
     • 📜 ставки на доп. рынки и экспрессы+ — в общей «Истории ставок» с карточкой ставки
     • 🔗 «Поделиться» любой ставкой: друг открывает ссылку и ставит так же
   Подключается после plus.js и использует мосты window.MSP и MSP.plus.
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M || !M.U || !M.plus) return;
  const { api, post, toast, ok, fail, dialog, fmt, esc, haptic, state, U } = M;
  const P = M.plus;
  const tg = M.tg;
  const $ = (id) => document.getElementById(id);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const main = document.querySelector('main.main');
  const anchor = $('history');
  const panel = (id, html) => { const s = document.createElement('section'); s.id = id; s.className = 'panel hidden'; s.innerHTML = html; main.insertBefore(s, anchor); return s; };
  const back = (to, label) => `<button class="back" type="button" data-tab="${to}">← ${label}</button>`;
  const head = (t, p) => `<div class="panel-head"><h2>${t}</h2>${p ? `<p>${p}</p>` : ''}</div>`;
  const navTo = (tab) => $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  const short = (n) => { n = Number(n) || 0; const f = (v) => (Math.round(v * 10) / 10).toString().replace('.', ','); return n >= 1e6 ? f(n / 1e6) + 'M' : n >= 1e3 ? f(n / 1e3) + 'K' : String(n); };
  const dt = (iso) => { try { return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const o2 = (v) => Number(v || 0).toFixed(2);
  const loading = '<div class="skeleton"></div><div class="skeleton"></div>';
  const pay = (amount, odds) => Math.floor(Number(amount) * Math.round(Number(odds) * 100) / 100);
  const pickText = (title, c) => { try { return M.pickText(title, c); } catch (_) { return { '1': 'П1', X: 'Ничья', '2': 'П2' }[c] || c; } };
  const plural = (n, a, b, c) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? b : c; };
  const daysLeft = (iso) => { const ms = new Date(iso) - Date.now(); if (ms <= 0) return 'сегодня'; const d = Math.floor(ms / 864e5), h = Math.floor(ms % 864e5 / 36e5); return d ? `${d} ${plural(d, 'день', 'дня', 'дней')}` : `${h} ч`; };

  const W = { sum: null, at: 0, loading: null, rules: null, hist: [], fb: {}, perks: {}, sp: null };
  const DEF_RULES = { boost: { 3: 5, 4: 10, 5: 15, 6: 20, 7: 25, 8: 30, 9: 35, 10: 40, 11: 45, 12: 50 }, boost_min_odds: 1.3, boost_cap: 1000000, ins_min_legs: 4, ins_min_odds: 1.3, ins_cap: 100000, fb_days: 7 };
  const R = () => W.rules || DEF_RULES;

  // ==========================================================
  //  Сводка: правила, фрибеты, пропуск (один лёгкий запрос, кэш 20 с)
  // ==========================================================
  function loadSum(force) {
    if (!force && W.sum && Date.now() - W.at < 20000) return Promise.resolve(W.sum);
    if (W.loading) return W.loading;
    W.loading = api('/api/v2/wave1/summary').then((d) => { W.sum = d; W.rules = d.rules; W.at = Date.now(); syncUi(); return d; })
      .catch(() => W.sum).finally(() => { W.loading = null; });
    return W.loading;
  }
  const fbList = () => (W.sum && W.sum.freebets) || [];
  function syncUi() {
    const n = fbList().length;
    $$('.w1-fb-btn').forEach((b) => { b.classList.toggle('hidden', !n); const c = b.querySelector('i'); if (c) c.textContent = n; });
    perkLines();
    passBanner();
  }

  // ==========================================================
  //  🔥 Бонус к экспрессу и 🛡 страховка: подсказки в купонах
  // ==========================================================
  function boostPct(n) { const b = R().boost; const max = Math.max(...Object.keys(b).map(Number)); return n >= 3 ? Number(b[Math.min(n, max)] || 0) : 0; }
  function perkHtml(odds, amount) {
    if (odds.length < 2) return '';
    const r = R();
    const n = odds.filter((o) => o >= r.boost_min_odds).length;
    const pct = boostPct(n);
    const total = Math.round(odds.reduce((a, o) => a * o, 1) * 100) / 100;
    const lines = [];
    if (pct) {
      const bonus = Math.min(r.boost_cap, Math.floor(Math.max(0, pay(amount, total) - amount) * pct / 100));
      const next = boostPct(n + 1);
      lines.push(`<div class="w1-perk on">🔥 <span>Бонус экспресса <b>+${pct}%</b>${amount >= 1 ? ` · <b>+${fmt(bonus)}</b> PTS к выигрышу` : ''}${next > pct && odds.length < 12 ? `<small>ещё 1 событие с кф ≥ ${o2(r.boost_min_odds)} → +${next}%</small>` : ''}</span></div>`);
    } else {
      lines.push(`<div class="w1-perk">🔥 <span>Ещё ${3 - n} ${plural(3 - n, 'событие', 'события', 'событий')} с кф ≥ ${o2(r.boost_min_odds)} — и бонус <b>+5%</b> к выигрышу<small>до +50% за 12 событий</small></span></div>`);
    }
    const ins = odds.length >= r.ins_min_legs && odds.every((o) => o >= r.ins_min_odds);
    lines.push(ins
      ? `<div class="w1-perk on">🛡 <span>Застрахован: не зайдёт одно событие — вернём ставку фрибетом<small>до ${fmt(r.ins_cap)} PTS · ставки фрибетом не страхуются</small></span></div>`
      : `<div class="w1-perk">🛡 <span>Страховка — от ${r.ins_min_legs} событий, у каждого кф ≥ ${o2(r.ins_min_odds)}</span></div>`);
    return lines.join('');
  }
  function perkBox(parent, id, beforeSel) {
    if (!parent) return null;
    let box = $(id);
    if (!box) { box = document.createElement('div'); box.id = id; box.className = 'w1-perks'; const ref = parent.querySelector(beforeSel); parent.insertBefore(box, ref || null); }
    return box;
  }
  function perkLines() {
    const mxp = $('mx-panel');
    if (mxp) {
      const box = perkBox(mxp.querySelector('.express-body'), 'w1-mx-perk', '.express-foot');
      const legs = P.MX.legs.filter((l) => !l.closed);
      if (box) { const h = perkHtml(legs.map((l) => Number(l.odds)), Math.floor(Number(($('mx-amount') || {}).value)) || 0); if (box.dataset.h !== h) { box.innerHTML = h; box.dataset.h = h; } }
      fbButton(mxp.querySelector('.btn-row'), 'w1-fb-mx');
    }
    const exp = $('express-body');
    if (exp) {
      const box = perkBox(exp, 'w1-ex-perk', '.express-foot');
      const h = perkHtml((state.cart || []).map((i) => Number(i.odds)), Math.floor(Number(($('express-amount') || {}).value)) || 0);
      if (box && box.dataset.h !== h) { box.innerHTML = h; box.dataset.h = h; }
    }
  }
  function fbButton(row, action) {
    if (!row || row.querySelector(`[data-action="${action}"]`)) return;
    row.insertAdjacentHTML('beforeend', `<button class="btn w1-fb-btn${fbList().length ? '' : ' hidden'}" type="button" data-action="${action}">🎁 Фрибетом <i>${fbList().length}</i></button>`);
  }
  const watch = (el, fn, opts = { childList: true, subtree: true }) => { if (el) new MutationObserver(fn).observe(el, opts); };
  let perkT = 0;
  const perkSoon = () => { clearTimeout(perkT); perkT = setTimeout(() => { perkLines(); if ((P.MX.legs.length || (state.cart || []).length)) loadSum(); }, 40); };
  watch($('cart-list'), perkSoon);
  document.addEventListener('input', (e) => { if (e.target && (e.target.id === 'mx-amount' || e.target.id === 'express-amount')) perkSoon(); });
  // купон экспресса+ создаётся лениво — ждём его появления
  const waitMx = new MutationObserver(() => { const p = $('mx-panel'); if (p) { waitMx.disconnect(); watch($('mx-list'), perkSoon); perkSoon(); } });
  if ($('mx-panel')) { watch($('mx-list'), perkSoon); perkSoon(); } else waitMx.observe(document.body, { childList: true, subtree: true });

  // ==========================================================
  //  🎁 Фрибеты: выбор жетона и ставка
  // ==========================================================
  function ensureFbModal() {
    let m = $('w1-fb-modal');
    if (m) return m;
    m = document.createElement('div');
    m.id = 'w1-fb-modal'; m.className = 'modal modal-center hidden'; m.dataset.action = 'w1-fb-backdrop';
    m.innerHTML = '<div class="sheet w1-fb-sheet" role="dialog" aria-modal="true"><div class="sheet-head"><div class="sheet-title">🎁 Поставить фрибетом</div><button class="icon-btn" type="button" data-action="w1-fb-cancel" aria-label="Закрыть"><svg class="ico"><use href="#i-x"/></svg></button></div><div id="w1-fb-pick"></div></div>';
    document.body.appendChild(m);
    return m;
  }
  let fbResolve = null;
  function fbPick(odds, what) {
    const list = fbList();
    if (!list.length) { toast('Фрибетов пока нет — их дают страховка экспресса и сезонный пропуск', 'err'); return Promise.resolve(null); }
    const m = ensureFbModal();
    $('w1-fb-pick').innerHTML = `<div class="w1-fb-what">${esc(what)} · кф <b>${o2(odds)}</b></div>` + list.map((f) => `
      <button type="button" class="w1-fb-item" data-action="w1-fb-choose" data-id="${f.id}">
        <span class="w1-fb-am">🎁 ${fmt(f.amount)}</span>
        <span class="w1-fb-tx"><b>Чистый выигрыш ${fmt(Math.max(0, pay(f.amount, odds) - f.amount))} PTS</b><small>${esc(f.source_label)} · сгорит через ${daysLeft(f.expires_at)}</small></span>
      </button>`).join('') + '<div class="muted-note">Баланс не списывается. При выигрыше получаешь выигрыш без суммы фрибета, при отмене матча фрибет вернётся.</div>';
    m.classList.remove('hidden');
    return new Promise((res) => { fbResolve = res; });
  }
  const fbClose = (v) => { const m = $('w1-fb-modal'); if (m) m.classList.add('hidden'); if (fbResolve) { const r = fbResolve; fbResolve = null; r(v); } };
  M.actions['w1-fb-choose'] = (el) => fbClose(fbList().find((f) => f.id === Number(el.dataset.id)) || null);
  M.actions['w1-fb-cancel'] = () => fbClose(null);
  M.actions['w1-fb-backdrop'] = (el, e) => { if (e.target === el) fbClose(null); };

  async function fbUse(body, fb) {
    const r = await post('/api/v2/freebets/use', { ...body, freebet_id: fb.id });
    haptic('success');
    ok(`🎁 Ставка фрибетом ${fmt(r.amount)} принята · кф ${o2(r.odds)} · чистый выигрыш ${fmt(r.net_win)} PTS`);
    U.after(r);
    W.sum.freebets = fbList().filter((f) => f.id !== fb.id); syncUi();
    loadSum(true);
    return r;
  }
  M.actions['w1-fb-mx'] = async (el) => {
    const legs = P.MX.legs;
    if (legs.length < 2) return toast('Минимум 2 события с разных матчей', 'err');
    if (legs.some((l) => l.closed)) return toast('Убери из купона закрытые матчи', 'err');
    await loadSum();
    const total = Math.round(legs.reduce((a, l) => a * l.odds, 1) * 100) / 100;
    const fb = await fbPick(total, `Экспресс+ из ${legs.length} ${plural(legs.length, 'события', 'событий', 'событий')}`);
    if (!fb) return;
    el.disabled = true;
    try {
      await fbUse({ type: 'mx', legs: legs.map(({ event_id, market, pick, odds }) => ({ event_id, market, pick, odds })) }, fb);
      P.MX.legs.splice(0); P.mxSave(); P.mxRender(); if (M.renderEvents) M.renderEvents();
    } catch (e) {
      fail(e);
      if (/изменил|закрыт/i.test(e.message) && P.mxQuote) P.mxQuote(true);
    } finally { el.disabled = false; }
  };
  M.actions['w1-fb-mk'] = async (el) => {
    const s = P.MK.sel, d = P.MK.ev;
    if (!s || !d) return;
    await loadSum();
    const fb = await fbPick(s.odds, `${d.title} — ${P.fullLabel(s.market, s.label)}`);
    if (!fb) return;
    el.disabled = true;
    try {
      await fbUse(s.market === '1x2' ? { type: 'single', event_id: d.event_id, choice: s.pick, odds: s.odds } : { type: 'market', event_id: d.event_id, market: s.market, pick: s.pick, odds: s.odds }, fb);
      P.MK.sel = null; await P.mkLoad(d.event_id);
    } catch (e) { fail(e); if (/изменил/i.test(e.message)) { try { await P.mkLoad(d.event_id, true); } catch (_) {} } } finally { el.disabled = false; }
  };
  M.actions['w1-fb-slip'] = async (el) => {
    const s = state.slip;
    if (!s) return;
    await loadSum();
    const ev = (state.events || []).find((x) => x.id === s.eventId);
    const fb = await fbPick(s.odds, `${ev ? ev.title + ' — ' : ''}${ev ? pickText(ev.title, s.choice) : s.choice}`);
    if (!fb) return;
    el.disabled = true;
    try {
      await fbUse({ type: 'single', event_id: s.eventId, choice: s.choice, odds: s.odds }, fb);
      $('bet-slip-modal').classList.add('hidden');
      M.loadMe().catch(() => {});
    } catch (e) { fail(e); } finally { el.disabled = false; }
  };
  // кнопка в старом купоне одинара
  const slipBtn = $('slip-submit-btn');
  if (slipBtn) slipBtn.insertAdjacentHTML('afterend', '<button class="btn btn-block w1-fb-btn w1-fb-slip hidden" type="button" data-action="w1-fb-slip">🎁 Поставить фрибетом <i>0</i></button>');
  watch($('bet-slip-modal'), () => { if (!$('bet-slip-modal').classList.contains('hidden')) loadSum(); }, { attributes: true, attributeFilter: ['class'] });
  // кнопка в окне доп. рынков (оно создаётся при первом открытии)
  const origMkOpen = M.actions['mk-open'];
  M.actions['mk-open'] = (el, e) => {
    const sh = $('w1-shared'); if (sh) sh.classList.add('hidden');
    const p = origMkOpen(el, e);
    fbButton(document.querySelector('#mk-slip .mk-slip-btns'), 'w1-fb-mk');
    loadSum();
    return p;
  };

  // ==========================================================
  //  📜 История ставок: доп. рынки и экспрессы+ в общем списке
  // ==========================================================
  const LEG_ICON = { won: '✅', lost: '❌', void: '↩️', pending: '⏳', cashed: '💸' };
  const LEG_TXT = { won: 'Зашло', lost: 'Не зашло', void: 'Возврат', pending: 'Ожидает' };
  const keyOf = (b) => `${b.kind}:${b.id}`;
  const amountOf = (b) => Number(b.points != null ? b.points : b.amount);
  const oddsOf = (b) => Number(b.locked_odds != null ? b.locked_odds : b.odds);
  function legOdds(b) { return (b.legs || []).map((l) => Number(l.odds)); }
  function tagsFor(b) {
    const t = [], fb = W.fb[keyOf(b)], pk = W.perks[keyOf(b)];
    if (fb) t.push(`🎁 Фрибет ${fmt(fb.amount)}`);
    if (pk && pk.perk === 'boost') t.push(`🔥 +${pk.pct}% · +${fmt(pk.amount)}`);
    if (pk && pk.perk === 'insurance') t.push(`🛡 Фрибет ${fmt(pk.amount)}`);
    if (!pk && b.status === 'pending' && (b.kind === 'express' || b.kind === 'mx')) {
      const odds = legOdds(b), r = R();
      const pct = boostPct(odds.filter((o) => o >= r.boost_min_odds).length);
      if (pct) t.push(`🔥 +${pct}%`);
      if (!fb && odds.length >= r.ins_min_legs && odds.every((o) => o >= r.ins_min_odds)) t.push('🛡 Страховка');
    }
    return t.map((x) => `<span class="hist-tag w1-tag">${esc(x)}</span>`).join('');
  }
  function newHistBtn(b, i) {
    const cls = b.status === 'won' ? 'won' : b.status === 'lost' ? 'lost' : 'pending';
    const fb = W.fb[keyOf(b)];
    const st = b.status === 'won' ? `Выиграл: +${fmt(fb ? b.payout - fb.amount : b.payout)} PTS` : b.status === 'lost' ? 'Проиграл' : b.status === 'void' ? 'Возврат ставки' : 'В ожидании';
    const won = b.legs.filter((l) => l.status === 'won').length;
    const what = b.kind === 'mx' ? `Экспресс+ · ${b.legs.length} ${plural(b.legs.length, 'событие', 'события', 'событий')} · Зашло ${won} из ${b.legs.length}` : 'Исход: ' + esc(b.legs[0] ? b.legs[0].label : '');
    return `<button type="button" class="hist ${cls} w1-hist" data-action="w1-bet-open" data-i="${i}" data-w1="1">
      <div class="hist-top"><span class="hist-sport">${b.kind === 'mx' ? '🚀 Экспресс+' : '📊 ' + esc(b.sport || 'Доп. рынок')}</span><span class="hist-meta">Кф ${o2(b.odds)}</span></div>
      <div class="hist-title">${esc(b.title)}</div>
      <div class="hist-meta">${what} · Ставка: ${fmt(b.amount)} PTS${b.legs[0] && b.legs[0].score && b.kind === 'market' ? ` · Счёт ${esc(b.legs[0].score)}` : ''}</div>
      <div class="hist-status">${st}</div>${tagsFor(b)}
    </button>`;
  }
  let merging = false, mergeSeq = 0;
  async function histMerge() {
    const list = $('history-list');
    if (!list || merging) return;
    const my = ++mergeSeq;
    let d;
    try { d = await api('/api/v2/betcards/history'); } catch (_) { return; }
    if (my !== mergeSeq || list.querySelector('[data-w1-mark]') || list.querySelector('.skeleton')) return;
    merging = true;
    try {
      W.hist = d.bets || []; W.fb = d.freebets || {}; W.perks = d.perks || {}; if (d.rules) W.rules = d.rules;
      const items = [];
      $$('#history-list .hist[data-action="bet-open"]').forEach((n) => {
        const h = (state.history || [])[Number(n.dataset.idx)];
        if (!h) return;
        if (!n.querySelector('.w1-tag')) n.insertAdjacentHTML('beforeend', tagsFor(h));
        items.push({ t: h.created_at || '', n });
      });
      const tmp = document.createElement('div');
      tmp.innerHTML = W.hist.map(newHistBtn).join('');
      Array.from(tmp.children).forEach((n, i) => items.push({ t: W.hist[i].created_at || '', n }));
      if (W.hist.length) {
        items.sort((a, b) => (a.t < b.t ? 1 : a.t > b.t ? -1 : 0));
        list.replaceChildren(...items.map((x) => x.n));
      }
      const mark = document.createElement('i'); mark.hidden = true; mark.dataset.w1Mark = '1';
      list.appendChild(mark);
    } finally { merging = false; }
  }
  // старый код перерисовывает историю (открытие раздела, выкуп) — каждый раз доклеиваем новые рынки
  const histList = $('history-list');
  let histT = 0;
  watch(histList, () => {
    if (merging || !histList || histList.querySelector('[data-w1-mark]') || histList.querySelector('.skeleton')) return;
    if (histList.querySelector('.empty') && !state.history) return; // ошибка загрузки
    clearTimeout(histT); histT = setTimeout(histMerge, 30);
  }, { childList: true });
  const histHead = document.querySelector('#history .panel-head p');
  if (histHead) histHead.textContent = 'Все ставки: исходы, экспрессы, тоталы, форы, точный счёт и экспрессы+. Нажми на ставку — карточка и «Поделиться».';

  // ---------------- Карточка ставки ----------------
  function perksBlock(b) {
    const fb = W.fb[keyOf(b)], pk = W.perks[keyOf(b)], r = R(), rows = [];
    if (fb) rows.push(`🎁 Ставка фрибетом ${fmt(fb.amount)} PTS — при выигрыше начисляется чистый выигрыш, сумма фрибета не возвращается${fb.state === 'debt' ? ` · к удержанию ещё ${fmt(fb.owed)} PTS` : ''}`);
    if (pk && pk.perk === 'boost') rows.push(`🔥 Бонус к экспрессу +${pk.pct}%: начислено +${fmt(pk.amount)} PTS`);
    if (pk && pk.perk === 'insurance') rows.push(`🛡 Страховка экспресса: ставка возвращена фрибетом ${fmt(pk.amount)} PTS`);
    if (!pk && b.status === 'pending' && (b.kind === 'express' || b.kind === 'mx')) {
      const odds = legOdds(b), pct = boostPct(odds.filter((o) => o >= r.boost_min_odds).length);
      const amount = amountOf(b);
      if (pct) rows.push(`🔥 Зайдёт — бонус +${pct}% к чистому выигрышу (≈ +${fmt(Math.min(r.boost_cap, Math.floor(Math.max(0, pay(amount, oddsOf(b)) - amount) * pct / 100)))} PTS)`);
      if (!fb && odds.length >= r.ins_min_legs && odds.every((o) => o >= r.ins_min_odds)) rows.push(`🛡 Застрахован: не зайдёт одно событие — вернём ${fmt(Math.min(amount, r.ins_cap))} PTS фрибетом`);
    }
    return rows.length ? `<div class="w1-card-perks">${rows.map((x) => `<div>${esc(x)}</div>`).join('')}</div>` : '';
  }
  function decorateOldCard() {
    const h = state.bet;
    if (!h || h._w1) return;
    const legs = $('bet-card-legs');
    if (legs && !legs.querySelector('.w1-card-perks')) legs.insertAdjacentHTML('beforeend', perksBlock(h));
    if (W.fb[keyOf(h)]) $('bet-cashout-btn').classList.add('hidden');
  }
  window.addEventListener('click', (e) => {
    if (e.target.closest && e.target.closest('[data-action="bet-open"]')) setTimeout(decorateOldCard, 0);
  }, true);
  // свежая цена выкупа приходит позже — ставкам фрибетом выкуп не показываем
  watch($('bet-cashout-btn'), () => { const h = state.bet, b = $('bet-cashout-btn'); if (h && (h._w1 || W.fb[keyOf(h)]) && !b.classList.contains('hidden')) b.classList.add('hidden'); }, { attributes: true, attributeFilter: ['class'] });

  function openNewCard(b) {
    state.bet = { ...b, _w1: true };
    const fb = W.fb[keyOf(b)], mx = b.kind === 'mx';
    $('bet-card-title').textContent = b.title;
    $('bet-card-sport').textContent = mx ? '🚀 Экспресс+' : `📊 Доп. рынок${b.sport ? ' · ' + b.sport : ''}`;
    $('bet-card-choice').textContent = mx ? `Экспресс+ · ${b.legs.length}` : (b.legs[0] ? b.legs[0].label : '—');
    $('bet-card-points').textContent = fb ? `🎁 ${fmt(b.amount)} фрибет` : `${fmt(b.amount)} PTS`;
    $('bet-card-odds').textContent = o2(b.odds);
    const label = $('bet-card-win-label'), win = $('bet-card-win');
    const net = (v) => (fb ? Math.max(0, v - b.amount) : v);
    if (b.status === 'lost') { label.textContent = 'Выигрыш'; win.textContent = '0 PTS'; }
    else if (b.status === 'void') { label.textContent = 'Возврат'; win.textContent = fb ? 'фрибет' : `${fmt(b.amount)} PTS`; }
    else if (b.status === 'won') { label.textContent = 'Выигрыш'; win.textContent = `${fmt(net(b.payout))} PTS`; }
    else { label.textContent = 'Возможный выигрыш'; win.textContent = `${fmt(net(pay(b.amount, b.odds)))} PTS`; }
    $('bet-card-banner').className = 'bet-banner' + (b.status === 'won' ? ' won' : b.status === 'lost' ? ' lost' : '');
    $('bet-card-status').textContent = { won: 'Выигрыш', lost: 'Проигрыш', void: 'Возврат' }[b.status] || 'В ожидании';
    const won = b.legs.filter((l) => l.status === 'won').length;
    $('bet-card-legs').innerHTML = `<div class="bet-legs-head"><span>${mx ? 'Исходы событий' : 'Итог матча'}</span><span>${mx ? `Зашло ${won} из ${b.legs.length}` : ''}</span></div>` + b.legs.map((l) => `
      <div class="leg ${l.status}">
        <span class="leg-ic">${LEG_ICON[l.status] || '⏳'}</span>
        <div class="leg-main">
          ${mx ? `<div class="leg-title">${esc(l.title)}</div>` : ''}
          <div class="leg-meta">Твой исход: <b>${esc(l.label)}</b> · кф ${o2(l.odds)}${l.score ? ` · Счёт: <b>${esc(l.score)}</b>` : ''}</div>
        </div>
        <span class="leg-st">${LEG_TXT[l.status] || ''}</span>
      </div>`).join('') + perksBlock(b);
    $('bet-cashout-btn').classList.add('hidden');
    $('bet-modal').classList.remove('hidden');
  }
  M.actions['w1-bet-open'] = (el) => { const b = W.hist[Number(el.dataset.i)]; if (b) { haptic('light'); openNewCard(b); } };

  // ==========================================================
  //  🔗 Поделиться ставкой (любой вид) — ссылка, по которой друг поставит так же
  // ==========================================================
  const BOT_LINK = 'https://t.me/mspbetsbot';
  function shareText(b) {
    const amount = amountOf(b), odds = oddsOf(b), fb = W.fb[keyOf(b)];
    const legs = b.legs || [];
    const lab = (l) => (b.kind === 'single' || b.kind === 'express' ? pickText(l.title, l.choice) : l.label);
    let body;
    if (legs.length > 1) {
      const t = b.kind === 'mx' ? '🚀 Мой экспресс+ в MSPBETS!' : '🔥 Мой экспресс в MSPBETS!';
      body = `${t}\n\n${legs.map((l, i) => `${i + 1}. ${l.title}\n    ✅ ${lab(l)} · кф ${o2(l.odds)}`).join('\n')}\n\n💰 Ставка: ${fmt(amount)} PTS${fb ? ' (фрибет)' : ''}\n📈 Общий кф: ${o2(odds)}`;
    } else {
      const l = legs[0] || { title: b.title, choice: b.choice, label: b.choice };
      body = `🔥 Моя ставка в MSPBETS!\n\n${b.sport && b.kind === 'single' ? `🏆 ${b.sport}\n` : ''}⚔️ ${l.title || b.title}\n✅ ${b.kind === 'market' ? 'Рынок' : 'Мой исход'}: ${lab(l)}\n💰 Ставка: ${fmt(amount)} PTS${fb ? ' (фрибет)' : ''}\n📈 Кф: ${o2(odds)}`;
    }
    const pk = W.perks[keyOf(b)];
    if (b.status === 'won') body += `\n🏆 Зашла! Выигрыш: +${fmt(b.kind === 'single' || b.kind === 'express' ? pay(amount, odds) : b.payout)} PTS${pk && pk.perk === 'boost' ? ` и бонус +${pk.pct}%` : ''}`;
    else if (b.status === 'pending') body += '\n\n👉 Открой ссылку и поставь так же в один клик:';
    else body += '\n\n👉 Заходи и прогнозируй:';
    return body;
  }
  async function shareBet(b) {
    if (!b) return;
    let link = BOT_LINK;
    try { link = (await post('/api/v2/betcards/share', { kind: b.kind, id: b.id })).link; } catch (_) {}
    const text = shareText(b);
    try { if (tg && tg.openTelegramLink) { tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`); return; } } catch (_) {}
    if (await U.copyText(`${text}\n${link}`)) ok('Текст и ссылка скопированы'); else toast('Не удалось поделиться', 'err');
  }
  window.addEventListener('click', (e) => {
    const el = e.target.closest && e.target.closest('#bet-modal [data-action="share"]');
    if (!el || !state.bet) return;
    e.stopPropagation(); e.preventDefault();
    shareBet(state.bet);
  }, true);

  // Открыть ставку по ссылке t.me/<бот>?startapp=bet_…
  function ensureShared() {
    let m = $('w1-shared');
    if (m) return m;
    m = document.createElement('div');
    m.id = 'w1-shared'; m.className = 'modal modal-center hidden'; m.dataset.action = 'w1-sh-backdrop';
    m.innerHTML = '<div class="sheet w1-sh-sheet" role="dialog" aria-modal="true"><div class="sheet-head"><div class="sheet-title">🔗 Ставка друга</div><button class="icon-btn" type="button" data-action="w1-sh-close" aria-label="Закрыть"><svg class="ico"><use href="#i-x"/></svg></button></div><div id="w1-sh-body"></div></div>';
    document.body.appendChild(m);
    return m;
  }
  async function openShared(p) {
    const m = ensureShared();
    $('w1-sh-body').innerHTML = loading;
    m.classList.remove('hidden');
    try {
      const [b] = await Promise.all([api('/api/v2/betcards/shared?p=' + encodeURIComponent(p)), P.loadAvail()]);
      W.sp = b;
      const extra = [];
      if (b.freebet) extra.push('🎁 сделана фрибетом');
      if (b.perk && b.perk.perk === 'boost') extra.push(`🔥 бонус +${b.perk.pct}%`);
      if (b.perk && b.perk.perk === 'insurance') extra.push('🛡 сработала страховка');
      $('w1-sh-body').innerHTML = P.betCard(b, { who: true, markets: !b.me }) + (extra.length ? `<div class="muted-note">${extra.join(' · ')}</div>` : '') +
        (b.me ? '<div class="muted-note">Это твоя ставка — так её видят друзья по ссылке.</div>'
          : b.copyable ? `<button class="btn btn-primary btn-block" type="button" data-action="w1-sh-copy">Поставить так же · кф ${o2(b.odds)}</button><div class="muted-note">Ставка будет сделана по текущим коэффициентам. 📊 — все рынки матча.</div>`
            : '<div class="muted-note">Матчи уже начались или ставка рассчитана — поставить так же нельзя. Загляни в «Матчи»!</div>');
    } catch (e) { $('w1-sh-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  M.actions['w1-sh-close'] = () => $('w1-shared').classList.add('hidden');
  M.actions['w1-sh-backdrop'] = (el, e) => { if (e.target === el) el.classList.add('hidden'); };
  M.actions['w1-sh-copy'] = async (el) => {
    const b = W.sp; if (!b) return;
    const v = await dialog({ title: 'Поставить так же', text: b.legs.length > 1 ? `Экспресс из ${b.legs.length} событий` : `${b.legs[0].title} — ${b.legs[0].label}`, fields: [{ name: 'a', label: 'Сумма, PTS', value: '100', type: 'number' }], okText: 'Поставить' });
    if (!v) return;
    el.disabled = true;
    try { const r = await post('/api/v2/betcards/shared/copy', { p: b.p, amount: Number(v.a) }); ok(`Ставка принята · кф ${o2(r.odds)}`); U.after(r); $('w1-shared').classList.add('hidden'); }
    catch (e) { fail(e); } finally { el.disabled = false; }
  };
  try {
    const sp = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    if (sp && /^bet_[semx]\d{1,12}_[0-9a-f]{12}$/.test(sp)) setTimeout(() => openShared(sp), 1200);
  } catch (_) {}
  M.openSharedBet = openShared;

  // ==========================================================
  //  🎁 Раздел «Мои фрибеты»
  // ==========================================================
  panel('freebets', back('profile', 'Профиль') + head('🎁 Фрибеты', 'Бесплатные ставки: выигрыш твой, сумма фрибета не возвращается') + '<div id="w1-fb-body"></div>');
  M.tabs.freebets = async () => {
    navTo('profile');
    const box = $('w1-fb-body');
    box.innerHTML = loading;
    const d = await api('/api/v2/freebets');
    const act = d.active.length ? d.active.map((f) => `<div class="w1-fb-card"><b>🎁 ${fmt(f.amount)}</b><span>${esc(f.source_label)}${f.note ? `<small>${esc(f.note)}</small>` : ''}</span><em>${daysLeft(f.expires_at)}</em></div>`).join('')
      : '<div class="empty">Активных фрибетов нет.<br>Их дают 🛡 страховка экспресса и 🎟 сезонный пропуск.</div>';
    const hist = d.history.map((f) => `<div class="tx"><span class="ic">${f.status === 'used' ? '🎯' : '⌛'}</span><div class="tt"><b>${fmt(f.amount)} · ${esc(f.source_label)}</b><small>${f.status === 'used' ? 'использован ' + dt(f.used_at) : 'сгорел'}</small></div></div>`).join('');
    box.innerHTML = (d.active.length ? `<div class="w1-fb-total">Всего: <b>${fmt(d.total)} PTS</b> в ${d.active.length} ${plural(d.active.length, 'фрибете', 'фрибетах', 'фрибетах')}</div>` : '') + act +
      `<div class="gcard w1-howto"><b>Как поставить фрибетом</b><ol><li>Открой матч и выбери исход — или «📊 Тоталы, форы» для доп. рынков.</li><li>В купоне нажми <b>«🎁 Фрибетом»</b> и выбери жетон.</li><li>Работает для П1/X/П2, доп. рынков и экспресса+.</li></ol><small>Выиграл — получаешь выигрыш без суммы фрибета. Матч отменён — фрибет вернётся. Срок жизни: страховка ${d.days} дн., пропуск 14 дн.</small></div>` +
      (hist ? '<div class="sub-head">История</div>' + hist : '');
    loadSum(true);
  };

  // ==========================================================
  //  🎟 Сезонный пропуск
  // ==========================================================
  panel('spass', back('bonus', 'Бонусы') + head('🎟 Сезонный пропуск', 'Делай ставки — получай уровни и награды. Каждый месяц новый сезон.') + '<div id="w1-pass-body"></div>');
  const rw = (r) => (r.type === 'freebet' ? `🎁 ${short(r.amount)}<small>фрибет</small>` : `💰 ${short(r.amount)}<small>PTS</small>`);
  async function passLoad() {
    const d = await api('/api/v2/pass');
    const box = $('w1-pass-body');
    const pct = d.level >= d.max_level ? 100 : Math.min(100, d.into_level / d.level_points * 100);
    const t = d.totals;
    const top = `<div class="gcard w1-pass-top">
        <div class="w1-pass-row"><div><small>Сезон</small><b>${esc(d.title)}</b></div><div class="w1-pass-lv"><small>Уровень</small><b>${d.level}<i>/${d.max_level}</i></b></div><div><small>До конца</small><b>${daysLeft(d.ends_at)}</b></div></div>
        <div class="bar w1-pass-bar"><i style="width:${pct}%"></i></div>
        <div class="w1-pass-sub">${d.level >= d.max_level ? '🏆 Максимальный уровень!' : `${d.into_level} / ${d.level_points} очков до уровня ${d.level + 1}`} · всего ${fmt(d.points)} очков</div>
        <div class="muted-note">1 очко = ${fmt(d.sport_rate)} PTS в ставках на матчи или ${fmt(d.game_rate)} PTS в играх. Ставки фрибетом очков не дают. В этом сезоне: матчи ${fmt(d.sport_wager)}, игры ${fmt(d.game_wager)}.</div>
        ${d.claimable ? `<button class="btn btn-primary btn-block" type="button" data-action="w1-pass-claim">Забрать награды (${d.claimable})</button>` : ''}
        ${d.premium ? '<div class="w1-prem-on">👑 Премиум активен</div>' : `<button class="btn btn-block w1-prem-btn" type="button" data-action="w1-pass-buy" data-price="${d.price}">👑 Премиум за ${fmt(d.price)} PTS<small>ещё ${short(t.premium.pts)} PTS и фрибеты на ${short(t.premium.freebet)} · награды за пройденные уровни сразу</small></button>`}
      </div>`;
    const rows = d.levels.map((l) => {
      const reached = l.level <= d.level;
      const cell = (tr, r, claimed, locked) => `<div class="w1-rw ${tr}${claimed ? ' got' : ''}${locked ? ' locked' : ''}${reached && !claimed && !locked ? ' ready' : ''}">${rw(r)}${claimed ? '<em>✓</em>' : locked ? '<em>🔒</em>' : ''}</div>`;
      return `<div class="w1-lv${reached ? ' on' : ''}${l.level === d.level + 1 ? ' next' : ''}"><span class="w1-lv-n">${l.level}</span>${cell('free', l.free, l.free_claimed, false)}${cell('prem', l.premium, l.premium_claimed, !d.premium)}</div>`;
    }).join('');
    box.innerHTML = top + `<div class="w1-track-head"><span></span><span>Бесплатно</span><span>👑 Премиум</span></div><div class="w1-track">${rows}</div>
      <div class="muted-note">За сезон: бесплатно ${fmt(t.free.pts)} PTS + фрибеты на ${fmt(t.free.freebet)}; премиум ещё ${fmt(t.premium.pts)} PTS + фрибеты на ${fmt(t.premium.freebet)}. Фрибеты пропуска живут ${d.fb_days} дн.</div>`;
    const nx = box.querySelector('.w1-lv.next');
    if (nx && d.level > 3) setTimeout(() => nx.scrollIntoView({ block: 'center', behavior: 'smooth' }), 120);
  }
  M.tabs.spass = async () => { navTo('bonus'); $('w1-pass-body').innerHTML = loading; await passLoad(); };
  M.actions['w1-pass-claim'] = async (el) => {
    el.disabled = true;
    try { const r = await post('/api/v2/pass/claim'); haptic('success'); ok('🎟 ' + r.message); U.after(r); await passLoad(); loadSum(true); }
    catch (e) { fail(e); el.disabled = false; }
  };
  M.actions['w1-pass-buy'] = async (el) => {
    const yes = await dialog({ title: '👑 Премиум-пропуск', text: `Стоимость ${fmt(Number(el.dataset.price))} PTS до конца сезона. Откроются премиум-награды всех уровней — за уже пройденные можно забрать сразу.`, okText: 'Купить' });
    if (!yes) return;
    el.disabled = true;
    try { const r = await post('/api/v2/pass/buy'); haptic('success'); ok('👑 Премиум активирован!'); U.setBal(r.balance); await passLoad(); loadSum(true); }
    catch (e) { fail(e); el.disabled = false; }
  };
  // баннер в «Бонусах»
  const bonusBody = $('bonus-body');
  if (bonusBody) bonusBody.insertAdjacentHTML('beforebegin', '<div id="w1-pass-bonus"></div>');
  function passBanner() {
    const box = $('w1-pass-bonus'), s = W.sum && W.sum.pass;
    if (!box || !s) return;
    const fbN = fbList().length;
    box.innerHTML = `<button type="button" class="dw-banner w1-pass-banner${s.claimable ? ' ready' : ''}" data-tab="spass"><span class="dw-b-ic">🎟</span><span class="dw-b-t"><b>Сезонный пропуск · ${esc(s.title)}</b><small>${s.claimable ? `Можно забрать наград: ${s.claimable}` : `Уровень ${s.level} из ${s.max_level} · ${s.points % s.level_points}/${s.level_points} очков`}${s.premium ? ' · 👑' : ''}</small></span><span class="dw-b-go">${s.claimable ? 'Забрать' : '→'}</span></button>` +
      (fbN ? `<button type="button" class="dw-banner w1-fb-banner" data-tab="freebets"><span class="dw-b-ic">🎁</span><span class="dw-b-t"><b>Фрибеты: ${fbN}</b><small>На ${fmt(fbList().reduce((a, f) => a + f.amount, 0))} PTS · ближайший сгорит через ${daysLeft(fbList()[0].expires_at)}</small></span><span class="dw-b-go">→</span></button>` : '');
  }
  M.tabs.bonus = ((orig) => async () => { const p = loadSum(true); if (orig) await orig(); await p; passBanner(); })(M.tabs.bonus);
  // пункты меню профиля
  M.tabs.profile = ((orig) => async () => {
    if (orig) await orig();
    const menu = document.querySelector('#profile-body .menu');
    if (menu && !menu.querySelector('[data-tab="spass"]')) {
      const h = menu.querySelector('[data-tab="history"]');
      const html = '<button data-tab="spass"><span class="i">🎟</span>Сезонный пропуск<small>награды за ставки</small></button><button data-tab="freebets"><span class="i">🎁</span>Мои фрибеты<small>бесплатные ставки</small></button>';
      if (h) h.insertAdjacentHTML('afterend', html); else menu.insertAdjacentHTML('afterbegin', html);
    }
  })(M.tabs.profile);

  // ==========================================================
  //  Журнал: подписи для новых видов операций
  // ==========================================================
  const KIND = { freebet: ['🎁', 'Фрибет'], freebet_stake: ['🎁', 'Фрибет: сумма жетона'], acca_boost: ['🔥', 'Бонус к экспрессу'], pass_reward: ['🎟', 'Сезонный пропуск'], pass_buy: ['👑', 'Премиум-пропуск'] };
  const ledger = $('ledger-list');
  if (ledger) {
    const fix = () => $$('#ledger-list .tx').forEach((tx) => {
      if (tx.dataset.w1) return;
      tx.dataset.w1 = '1';
      const small = tx.querySelector('.tt small'), ic = tx.querySelector('.ic');
      if (!small || !ic) return;
      const k = Object.keys(KIND).find((x) => small.textContent.startsWith(x + ' ·'));
      if (k) { ic.textContent = KIND[k][0]; small.textContent = small.textContent.replace(k, KIND[k][1]); }
    });
    watch(ledger, fix, { childList: true });
  }

  setTimeout(() => loadSum(), 2500);
})();
