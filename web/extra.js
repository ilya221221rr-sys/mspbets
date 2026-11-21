/* ==========================================================
   MSPBETS — новые разделы: профиль/VIP, бонусы, задания, турниры, кейсы,
   апгрейдер, коллекции, журнал, достижения, рефералка, лента ставок.
   Использует мост window.MSP из app.js.
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M) return;
  const { api, post, toast, ok, fail, dialog, fmt, esc, haptic, sleep, state } = M;
  const $ = (id) => document.getElementById(id);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const main = document.querySelector('main.main');
  const anchor = $('history');

  function panel(id, html) {
    const s = document.createElement('section');
    s.id = id; s.className = 'panel hidden'; s.innerHTML = html;
    main.insertBefore(s, anchor);
    return s;
  }
  const head = (t, p) => `<div class="panel-head"><h2>${t}</h2>${p ? `<p>${p}</p>` : ''}</div>`;
  const back = (to, label) => `<button class="back" type="button" data-tab="${to}">← ${label}</button>`;
  const AVA_OK = /^[a-z0-9_-]{1,24}$/;
  const ava = (name, frame, cls = '', avatar = '') => (avatar && AVA_OK.test(avatar)
    ? `<span class="ava ${cls} has-img ${frame ? 'fr-' + esc(frame) : ''}"><img src="/web/avatars/${avatar}.webp" alt="" loading="lazy" decoding="async"></span>`
    : `<span class="ava ${cls} ${frame ? 'fr-' + esc(frame) : ''}">${esc((name || '?').trim().charAt(0).toUpperCase())}</span>`);
  const dt = (iso) => { try { return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const loading = '<div class="skeleton"></div><div class="skeleton"></div>';
  const copyText = async (t) => {
    try { await navigator.clipboard.writeText(t); return true; } catch (_) {
      const a = document.createElement('textarea'); a.value = t; document.body.appendChild(a); a.select();
      try { document.execCommand('copy'); return true; } catch (e) { return false; } finally { a.remove(); }
    }
  };

  // ---------- общие помощники для всех модулей ----------
  let meTimer = 0;
  const U = {
    ava, esc, fmt, copyText,
    setBal(n) { if (n == null) return; M.animateBalance(Number(n)); if (state.me) state.me.balance = Number(n); },
    touchMe() { clearTimeout(meTimer); meTimer = setTimeout(() => M.loadMe().catch(() => {}), 1200); },
    after(res) { // применить ответ игры: баланс, достижения, обновить уровень
      if (!res) return;
      U.setBal(res.balance);
      if (res.unlocked && res.unlocked.length) U.popups(res.unlocked);
      U.touchMe();
    },
    async popups(list) {
      for (const a of list) {
        const el = document.createElement('div');
        el.className = 'unlock-pop';
        el.innerHTML = `<span class="ic">${esc(a.icon || '🏅')}</span><div><b>${esc(a.title)}</b><small>${a.reward ? '+' + fmt(a.reward) + ' PTS · ' : ''}${esc(a.desc || '')}</small></div>`;
        document.body.appendChild(el); haptic('success');
        await sleep(4100); el.remove();
      }
    },
    betBox(id, def = 100) {
      return `<label class="label" for="${id}">Ставка, PTS</label><div class="amount-row">
        <input id="${id}" class="input" type="number" min="1" value="${def}" inputmode="numeric">
        <button class="chip" type="button" data-action="bet-op" data-t="${id}" data-op="half">½</button>
        <button class="chip" type="button" data-action="bet-op" data-t="${id}" data-op="double">×2</button>
        <button class="chip" type="button" data-action="bet-op" data-t="${id}" data-op="max">MAX</button></div>`;
    },
    bet(id) { const v = Math.floor(Number($(id).value)); if (!(v >= 1)) throw new Error('Введите корректную ставку'); return v; },
  };
  M.U = U;
  M.actions['bet-op'] = (el) => {
    const i = $(el.dataset.t); const cur = Math.floor(Number(i.value)) || 1; const bal = Math.floor((state.me && state.me.balance) || cur);
    i.value = Math.max(1, el.dataset.op === 'half' ? Math.floor(cur / 2) : el.dataset.op === 'double' ? Math.min(cur * 2, bal) : bal);
  };
  M.actions.watch = (el) => M.watchToggle(Number(el.dataset.id));

  // ---------- шапка: уровень, красная точка ----------
  M.onMe = (me) => {
    let chip = $('lvl-chip');
    if (!chip) {
      chip = document.createElement('button');
      chip.id = 'lvl-chip'; chip.className = 'lvl-chip'; chip.type = 'button'; chip.dataset.tab = 'profile';
      document.querySelector('.topbar').insertBefore(chip, document.querySelector('.wallet'));
    }
    chip.innerHTML = `${ava(me.name, me.frame, 'sm', me.avatar)} LV <b>${me.level}</b>`;
    const dot = $('dot-bonus');
    if (dot) dot.classList.toggle('hidden', !((me.daily && !me.daily.claimed) || me.quests_claimable > 0));
  };

  // ==========================================================
  //  ИГРЫ: хаб (сами игры — в games.js)
  // ==========================================================
  panel('games', head('Игры', 'Все игры на баллы · честный RTP · результат считает сервер') + `
    <div class="hub">
      <button class="hub-card wide" data-tab="crash" style="--hc:#22D3EE"><span class="hub-ico">🚀</span><span class="hub-name">Краш</span><span class="hub-sub">Нажми «Играть» · забери до крэша · RTP 96%</span></button>
      <button class="hub-card wide" data-tab="slots" style="--hc:#FF5DAE"><span class="hub-ico">🎰</span><span class="hub-name">Слоты · демо</span><span class="hub-sub">Pragmatic Play, Hacksaw (Le Zeus) и другие · без трат PTS</span></button>
      <button class="hub-card" data-tab="mines" style="--hc:#FF5D7A"><span class="hub-ico">💣</span><span class="hub-name">Мины</span><span class="hub-sub">Поле 5×5 · RTP 97%</span></button>
      <button class="hub-card" data-tab="plinko" style="--hc:#B26BFF"><span class="hub-ico">🔮</span><span class="hub-name">Plinko</span><span class="hub-sub">До x1000 · RTP 96%</span></button>
      <button class="hub-card" data-tab="dice" style="--hc:#2EE59D"><span class="hub-ico">🎲</span><span class="hub-name">Dice</span><span class="hub-sub">Свой шанс · RTP 97%</span></button>
      <button class="hub-card" data-tab="wheel" style="--hc:#FFC93C"><span class="hub-ico">🎡</span><span class="hub-name">Колесо</span><span class="hub-sub">До x10 · RTP 95.8%</span></button>
      <button class="hub-card wide" data-tab="blackjack" style="--hc:#2EE59D"><span class="hub-ico">🃏</span><span class="hub-name">Блэкджек</span><span class="hub-sub">Набери 21 · блэкджек 3:2 · RTP ≈ 99%</span></button>
      <button class="hub-card" data-tab="coin" style="--hc:#FFA43C"><span class="hub-ico">🪙</span><span class="hub-name">Монетка</span><span class="hub-sub">x1.94 · RTP 97%</span></button>
    </div>
    <div class="muted-note" style="margin-top:14px">Только виртуальные баллы. Каждая ставка записывается в журнал операций и даёт XP.</div>`);

  // ==========================================================
  //  БОНУСЫ: ежедневный бонус, задания, турниры
  // ==========================================================
  panel('bonus', head('Бонусы', 'Ежедневная серия, задания и турниры') + '<div id="bonus-body">' + loading + '</div>');
  M.tabs.bonus = async () => {
    const [me, quests, tours] = await Promise.all([api('/api/v2/me'), api('/api/v2/quests'), api('/api/v2/tournaments')]);
    state.me = Object.assign(state.me || {}, me); M.onMe(state.me);
    const d = me.daily;
    const lastIdx = d.claimed ? (d.streak - 1) % 7 : d.next_index - 1;
    const days = d.cycle.map((r, i) => `<div class="day ${i <= lastIdx ? 'done' : ''} ${!d.claimed && i === d.next_index ? 'cur' : ''}">День ${i + 1}<b>${r}</b></div>`).join('');
    const vip = me.vip ? `<div class="muted-note">VIP ${me.vip}: бонус увеличен на ${me.vip * 25}%</div>` : '';
    $('bonus-body').innerHTML = `
      <div class="gcard">
        <div style="display:flex;justify-content:space-between;align-items:center"><div><div class="label">Ежедневный бонус</div><div class="streak-big">🔥 ${d.streak} <small style="font-size:13px;color:var(--muted)">дн. подряд</small></div></div>
        <div style="text-align:right"><div class="label">Рекорд</div><b>${d.best}</b></div></div>
        <div class="days">${days}</div>${vip}
        <button class="btn btn-primary btn-block" data-action="daily-claim" ${d.claimed ? 'disabled' : ''}>${d.claimed ? 'Получено сегодня ✓ — приходи завтра' : 'Забрать бонус'}</button>
      </div>
      <div class="sub-head">Задания дня</div>
      <div class="gcard">${quests.map((q) => `<div class="quest">
        <div class="quest-top"><span>${esc(q.title)}</span><span class="rw">+${fmt(q.reward)}</span></div>
        <div class="bar"><i style="width:${q.progress / q.target * 100}%"></i></div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px"><small class="muted-note">${q.progress}/${q.target}</small>
        ${q.claimed ? '<span class="pill">Получено ✓</span>' : `<button class="btn btn-sm ${q.progress >= q.target ? 'btn-success' : 'btn-ghost'}" data-action="quest-claim" data-key="${q.key}" ${q.progress >= q.target ? '' : 'disabled'}>Забрать</button>`}</div></div>`).join('')}</div>
      <div class="sub-head">Турниры недели</div>
      ${tours.filter((t) => t.status === 'active').map(tourCard).join('') || '<div class="empty">Турниры скоро появятся</div>'}
      <div class="muted-note">Очки турнира = сумма ваших ставок за неделю (матчи, игры, кейсы). Призы получают топ-3 (50% / 30% / 20% фонда).</div>`;
  };
  function tourCard(t) {
    const left = Math.max(0, new Date(t.ends_at) - Date.now()); const dd = Math.floor(left / 864e5), hh = Math.floor(left % 864e5 / 36e5);
    return `<div class="tour ${t.closed ? 'closed' : ''}">
      <div class="tour-head"><span>${t.closed ? '🔒 ' : '🏆 '}${esc(t.name)}</span><span class="pill">Фонд ${fmt(t.prize_pool)}</span></div>
      <small>${t.closed ? `Закрытый · нужен ${t.min_level} уровень` : 'Открытый турнир'} · осталось ${dd} д ${hh} ч · призы: ${t.prizes.map(fmt).join(' / ')}</small>
      ${t.joined ? `<div style="margin-top:8px;font-weight:800">Ваши очки: <span class="gold">${fmt(t.score)}</span>${t.rank ? ` · место #${t.rank}` : ''}</div>` : ''}
      ${t.top.length ? `<div class="lb">${t.top.slice(0, 5).map((r, i) => `<div><span>${['🥇', '🥈', '🥉', '4', '5'][i]}</span><span>${esc(r.name)}</span><b>${fmt(r.score)}</b></div>`).join('')}</div>` : ''}
      ${t.joined ? '' : `<button class="btn btn-block ${t.locked ? 'btn-ghost' : 'btn-primary'}" style="margin-top:10px" data-action="tour-join" data-id="${t.id}" ${t.locked ? 'disabled' : ''}>${t.locked ? `🔒 Нужен ${t.min_level} уровень` : 'Участвовать'}</button>`}
    </div>`;
  }
  M.actions['daily-claim'] = async () => {
    try { const r = await post('/api/v2/daily/claim'); ok(`+${fmt(r.reward)} PTS · серия ${r.streak} дн.`); U.after(r); await M.tabs.bonus(); } catch (e) { fail(e); }
  };
  M.actions['quest-claim'] = async (el) => {
    try { const r = await post('/api/v2/quests/claim', { key: el.dataset.key }); ok(`+${fmt(r.reward)} PTS за задание`); U.setBal(r.balance); await M.tabs.bonus(); } catch (e) { fail(e); }
  };
  M.actions['tour-join'] = async (el) => {
    try { await post('/api/v2/tournaments/join', { id: Number(el.dataset.id) }); ok('Вы в турнире! Делайте ставки'); await M.tabs.bonus(); } catch (e) { fail(e); }
  };

  // ==========================================================
  //  ПРОФИЛЬ
  // ==========================================================
  panel('profile', head('Профиль') + '<div id="profile-body">' + loading + '</div>');
  M.tabs.profile = async () => {
    const [me, ach] = await Promise.all([api('/api/v2/me'), api('/api/v2/achievements')]);
    state.me = Object.assign(state.me || {}, me); M.onMe(state.me); state.badgeCur = ach.badge;
    const pct = Math.min(100, (me.xp - me.xp_base) / Math.max(1, me.xp_next - me.xp_base) * 100);
    const ALLF = [['', 'Нет'], ['bronze', ''], ['silver', ''], ['gold', ''], ['platinum', ''], ['diamond', ''], ['legend', ''], ['streak', '']];
    const frames = ALLF.map(([f]) => `<span class="ava sm ${f ? 'fr-' + f : ''} ${me.frames.includes(f) || !f ? '' : ''}" data-action="frame-set" data-f="${f}" style="${me.frames.includes(f) || !f ? '' : 'filter:grayscale(1);opacity:.3'}" title="${f || 'без рамки'}"><i style="font-style:normal" class="${me.frame === f ? 'on' : ''}">${me.frame === f ? '✓' : (f ? '' : '∅')}</i></span>`).join('');
    const avatars = `<button type="button" class="none ${me.avatar ? '' : 'on'}" data-action="avatar-set" data-a="">Буква</button>` + (me.avatars || []).map((a) => `<button type="button" class="${me.avatar === a.key ? 'on' : ''}" data-action="avatar-set" data-a="${esc(a.key)}" title="${esc(a.title)}"><img src="/web/avatars/${esc(a.key)}.webp" alt="${esc(a.title)}" loading="lazy" decoding="async"><small>${esc(a.title)}</small></button>`).join('');
    const got = ach.items.filter((a) => a.unlocked);
    const badges = got.length ? got.map((a) => `<button type="button" class="${ach.badge === a.key ? 'on' : ''}" data-action="badge-set" data-k="${a.key}" title="${esc(a.title)}">${a.icon}</button>`).join('') : '<span class="muted-note" style="border:0;background:none;width:auto">Пока нет бейджей — выполняй достижения</span>';
    $('profile-body').innerHTML = `
      <div class="gcard">
        <div class="prof-head">${ava(me.name, me.frame, 'lg', me.avatar)}<div style="min-width:0"><div class="nm">${esc(me.name)} ${ach.badge ? (got.find((a) => a.key === ach.badge) || {}).icon || '' : ''}</div>
          <span class="rank-tag" style="--rc:${me.rank.color}">${esc(me.rank.name)}</span>${me.vip ? `<span class="vip">VIP ${me.vip}</span>` : ''}
          <div class="muted-note" style="margin-top:4px">${esc(me.bio || 'Добавь статус о себе')} <a data-action="bio-edit" style="color:var(--primary-2)">✎</a></div></div></div>
        <div style="margin-top:14px;display:flex;justify-content:space-between;font-size:12px;font-weight:700"><span>Уровень ${me.level}</span><span class="muted-note" style="border:0;background:none;width:auto">${fmt(me.xp)} / ${fmt(me.xp_next)} XP</span></div>
        <div class="bar" style="margin-top:6px"><i style="width:${pct}%"></i></div>
        <div class="muted-note" style="margin-top:8px">XP за каждую ставку. Новый уровень — бонус ${fmt((me.level + 1) * 50)} PTS. VIP с 20 уровня: бонус +25% и закрытые турниры.</div>
      </div>
      <div class="gcard"><div class="stat-row"><span>Поставлено всего</span><b>${fmt(me.stats.wagered)} PTS</b></div><div class="stat-row"><span>Ставок и игр</span><b>${fmt(me.stats.bets)}</b></div>
        <div class="stat-row"><span>Серия бонуса</span><b>🔥 ${me.daily.streak} (рекорд ${me.daily.best})</b></div></div>
      <div class="gcard"><div class="label">Аватар</div><div class="avatars">${avatars}</div>
        <div class="label" style="margin-top:14px">Рамка аватара</div><div class="frames">${frames}</div><div class="label" style="margin-top:14px">Бейдж</div><div class="badges">${badges}</div>
        <div class="toggle" style="margin-top:8px"><span>🔔 Уведомления в боте</span><button class="sw ${me.notify ? 'on' : ''}" data-action="toggle-notify" aria-label="переключить"></button></div>
        <div class="toggle"><span>🙈 Скрыть мои ставки от других</span><button class="sw ${me.hide_bets ? 'on' : ''}" data-action="toggle-hide" aria-label="переключить"></button></div></div>
      <div class="menu">
        <button data-tab="ledger"><span class="i">🧾</span>Журнал операций<small>все движения баллов</small></button>
        <button data-tab="history"><span class="i">🎯</span>История ставок</button>
        <button data-tab="achievements"><span class="i">🏅</span>Достижения<small>${got.length}/${ach.items.length}</small></button>
        <button data-tab="social"><span class="i">👥</span>Лента ставок игроков<small>копируй ставки</small></button>
        <button data-tab="referral"><span class="i">🤝</span>Пригласить друга<small>+${fmt(50000)} PTS</small></button>
        <button data-tab="rating"><span class="i">👑</span>Топ игроков</button>
        <button data-tab="news"><span class="i">📰</span>Новости Dota 2</button>
        <button data-tab="support"><span class="i">🛟</span>Поддержка<small id="sup-badge" class="sup-badge"></small></button>
        <button data-action="promo"><span class="i">🎟️</span>Ввести промокод</button>
        ${me.is_admin ? '<button data-tab="admin"><span class="i">🛡️</span>Админ-панель</button>' : ''}
      </div>`;
  };
  M.actions.promo = () => $('btn-promo').click();
  const setting = (body) => post('/api/v2/profile/settings', body).then(() => M.tabs.profile()).catch(fail);
  M.actions['avatar-set'] = (el) => setting({ avatar: el.dataset.a });
  M.actions['frame-set'] = (el) => setting({ frame: el.dataset.f });
  M.actions['badge-set'] = (el) => setting({ badge: state.badgeCur === el.dataset.k ? '' : el.dataset.k });
  M.actions['toggle-notify'] = () => setting({ notify: !state.me.notify });
  M.actions['toggle-hide'] = () => setting({ hide_bets: !state.me.hide_bets });
  M.actions['bio-edit'] = async () => {
    const v = await dialog({ title: 'О себе', fields: [{ name: 'bio', label: 'Статус (до 80 символов)', value: state.me.bio || '' }], okText: 'Сохранить' });
    if (v) setting({ bio: v.bio });
  };

  // ==========================================================
  //  ЖУРНАЛ ОПЕРАЦИЙ
  // ==========================================================
  const KIND = { bet: ['🎯', 'Ставка на матч'], win: ['🏆', 'Выигрыш'], refund: ['↩️', 'Возврат'], cashout: ['💸', 'Выкуп ставки'], game_bet: ['🎮', 'Ставка в игре'], game_win: ['🎰', 'Выигрыш в игре'], case: ['📦', 'Кейс'], sell: ['💱', 'Продажа предметов'], daily: ['📅', 'Ежедневный бонус'], quest: ['✅', 'Задание'], achievement: ['🏅', 'Достижение'], levelup: ['⭐', 'Новый уровень'], referral: ['🤝', 'Рефералка'], tournament: ['🏆', 'Турнир'], collection: ['🗂️', 'Коллекция'], promo: ['🎟️', 'Промокод'], admin: ['🛠️', 'Админ'] };
  panel('ledger', back('profile', 'Профиль') + head('Журнал операций', 'Любое движение баллов записано') + '<div id="ledger-list"></div><button id="ledger-more" class="btn btn-ghost btn-block hidden" data-action="ledger-more">Показать ещё</button>');
  let ledgerLast = 0;
  async function loadLedger(more) {
    if (!more) { $('ledger-list').innerHTML = loading; ledgerLast = 0; }
    const rows = await api('/api/v2/ledger' + (ledgerLast ? `?before=${ledgerLast}` : ''));
    const html = rows.map((r) => { const k = KIND[r.kind] || ['•', r.kind]; const plus = r.amount > 0;
      return `<div class="tx"><span class="ic">${k[0]}</span><div class="tt"><b>${esc(r.note || k[1])}</b><small>${k[1]} · ${dt(r.at)}</small></div><div class="am ${plus ? 'win' : 'lose'}">${plus ? '+' : ''}${fmt(r.amount)}<small>баланс ${fmt(r.balance_after)}</small></div></div>`; }).join('');
    if (!more) $('ledger-list').innerHTML = html || '<div class="empty">Операций пока нет</div>'; else $('ledger-list').insertAdjacentHTML('beforeend', html);
    if (rows.length) ledgerLast = rows[rows.length - 1].id;
    $('ledger-more').classList.toggle('hidden', rows.length < 40);
  }
  M.tabs.ledger = () => loadLedger(false);
  M.actions['ledger-more'] = () => loadLedger(true).catch(fail);

  // ==========================================================
  //  ДОСТИЖЕНИЯ
  // ==========================================================
  panel('achievements', back('profile', 'Профиль') + head('Достижения', 'Награда в баллах и бейдж для профиля') + '<div id="ach-body"></div>');
  M.tabs.achievements = async () => {
    const d = await api('/api/v2/achievements'); state.badgeCur = d.badge;
    $('ach-body').innerHTML = `<div class="ach-grid">${d.items.map((a) => `<div class="ach ${a.unlocked ? 'got' : 'locked'}"><div class="ic">${a.icon}</div><b>${esc(a.title)}</b><small>${esc(a.desc)}</small>
      ${a.unlocked ? '<div class="rw">✓ получено</div>' : `<div class="bar"><i style="width:${a.progress / a.target * 100}%"></i></div><div class="rw">${fmt(Math.min(a.progress, a.target))}/${fmt(a.target)} · +${fmt(a.reward)}</div>`}</div>`).join('')}</div>`;
  };

  // ==========================================================
  //  РЕФЕРАЛКА
  // ==========================================================
  panel('referral', back('profile', 'Профиль') + head('Пригласи друга', 'Баллы только за реальную активность друга') + '<div id="ref-body"></div>');
  M.tabs.referral = async () => {
    const r = await api('/api/v2/referral');
    $('ref-body').innerHTML = `<div class="gcard">
      <div class="stat-row"><span>Ты получишь</span><b class="gold">+${fmt(r.reward_you)} PTS</b></div><div class="stat-row"><span>Друг получит</span><b class="gold">+${fmt(r.reward_friend)} PTS</b></div>
      <div class="muted-note" style="margin-top:8px">Бонус начисляется после того, как друг (новый игрок) сделает минимум ${r.need_bets} ставок на сумму от ${fmt(r.need_wager)} PTS. Накрутка не даёт баллов.</div>
      <div class="ref-link" id="ref-link">${esc(r.link)}</div>
      <div class="btn-row"><button class="btn btn-ghost" data-action="ref-copy">Скопировать</button><button class="btn btn-primary" data-action="ref-share">Отправить другу</button></div>
      <div class="stat-row" style="margin-top:12px"><span>Заработано на рефералах</span><b class="gold">${fmt(r.earned)} PTS</b></div></div>
      <div class="sub-head">Мои друзья (${r.friends.length})</div>
      <div class="gcard">${r.friends.map((f) => `<div class="friend"><span class="nm">${esc(f.name)}</span>${f.paid ? '<span class="pill win">✓ бонус выдан</span>' : `<span class="muted-note" style="border:0;background:none;width:auto">${f.bets}/${r.need_bets} ставок · ${fmt(f.wagered)}/${fmt(r.need_wager)}</span>`}</div>`).join('') || '<div class="empty">Пока никого. Поделись ссылкой!</div>'}</div>`;
    state.refLink = r.link;
  };
  M.actions['ref-copy'] = async () => { (await copyText(state.refLink)) ? ok('Ссылка скопирована') : toast('Не удалось скопировать', 'err'); };
  M.actions['ref-share'] = () => {
    const text = '🏆 Залетай в MSPBETS — прогнозы, Краш, Мины и кейсы на виртуальные баллы!';
    const url = `https://t.me/share/url?url=${encodeURIComponent(state.refLink)}&text=${encodeURIComponent(text)}`;
    if (M.tg && M.tg.openTelegramLink) M.tg.openTelegramLink(url); else window.open(url, '_blank');
  };

  // ==========================================================
  //  ЛЕНТА СТАВОК И ПРОФИЛИ ИГРОКОВ (копирование ставок)
  // ==========================================================
  const CH = { '1': 'П1', X: 'Ничья', '2': 'П2' };
  panel('social', back('profile', 'Профиль') + head('Лента ставок', 'Копируй ставки других игроков по текущему кэфу') + '<div id="social-body"></div>');
  M.tabs.social = async () => {
    $('social-body').innerHTML = loading;
    const rows = await api('/api/v2/social/feed');
    $('social-body').innerHTML = rows.map((r) => `<div class="feed-bet">
      <div class="feed-top" data-action="player" data-uid="${r.uid}">${ava(r.name, r.frame, 'sm', r.avatar)}<span>${esc(r.name)}</span><span class="lv">LV ${r.level}</span></div>
      <div class="feed-ev">${esc(r.title)}</div>
      <div class="feed-meta">${esc(M.pickText(r.title, r.choice))} · ставка <b>${fmt(r.points)}</b> · кф <b>${Number(r.odds).toFixed(2)}</b>${r.odds_now && Math.abs(r.odds_now - r.odds) > 0.009 ? ` → сейчас <b class="${r.odds_now > r.odds ? 'up' : 'down'}">${Number(r.odds_now).toFixed(2)}</b>` : ''}</div>
      <button class="btn btn-sm btn-primary" data-action="copy-bet" data-id="${r.pred_id}" data-ev="${esc(r.title)}">Скопировать ставку</button></div>`).join('') || '<div class="empty">Пока нет ставок на открытые матчи</div>';
  };
  M.actions['copy-bet'] = async (el) => {
    const v = await dialog({ title: 'Скопировать ставку', text: el.dataset.ev + '\nСтавка будет сделана по текущему коэффициенту.', fields: [{ name: 'a', label: 'Сумма, PTS', value: '100', type: 'number' }], okText: 'Поставить' });
    if (!v) return;
    try { const r = await post('/api/v2/social/copy', { pred_id: Number(el.dataset.id), amount: Number(v.a) }); ok(`Ставка скопирована · кф ${Number(r.odds).toFixed(2)}`); U.after(r); } catch (e) { fail(e); }
  };
  panel('player', back('profile', 'Назад') + '<div id="player-body"></div>');
  async function loadPlayer(uid) {
    $('player-body').innerHTML = loading;
    const p = await api('/api/v2/player/' + uid); const s = p.stats;
    $('player-body').innerHTML = `<div class="gcard"><div class="prof-head">${ava(p.name, p.frame, 'lg', p.avatar)}<div><div class="nm">${esc(p.name)}</div>
      <span class="rank-tag" style="--rc:${p.rank.color}">${esc(p.rank.name)} · LV ${p.level}</span>${p.vip ? `<span class="vip">VIP ${p.vip}</span>` : ''}
      <div class="muted-note" style="margin-top:4px">${esc(p.bio || '')}</div></div></div>
      <div class="stat-row" style="margin-top:12px"><span>Ставок и игр</span><b>${fmt(s.bets)}</b></div><div class="stat-row"><span>Побед / поражений (матчи)</span><b>${s.won} / ${s.lost}${s.winrate != null ? ` · ${s.winrate}%` : ''}</b></div>
      <div class="stat-row"><span>Лучший выигрыш</span><b class="gold">${fmt(s.biggest_win)}</b></div><div class="stat-row"><span>Серия бонуса</span><b>🔥 ${s.streak} (рекорд ${s.best_streak})</b></div>
      ${p.badges.length ? `<div class="badges">${p.badges.map((b) => `<span title="${esc(b.title)}">${b.icon}</span>`).join('')}</div>` : ''}</div>
      <div class="sub-head">Последние ставки</div>
      ${p.hidden ? '<div class="empty">Игрок скрыл свои ставки</div>' : p.bets.map((b) => `<div class="feed-bet"><div class="feed-ev">${esc(b.title)}</div>
        <div class="feed-meta">${esc(M.pickText(b.title, b.choice))} · ${fmt(b.points)} PTS · кф ${Number(b.odds).toFixed(2)} · ${b.status === 'won' ? '<b class="up">выиграла</b>' : b.status === 'lost' ? '<b class="down">проиграла</b>' : b.status === 'void' ? 'возврат' : 'в игре'}</div>
        ${b.open && !p.me ? `<button class="btn btn-sm btn-primary" data-action="copy-bet" data-id="${b.pred_id}" data-ev="${esc(b.title)}">Скопировать</button>` : ''}</div>`).join('') || '<div class="empty">Ставок пока нет</div>'}`;
  }
  M.actions.player = async (el) => { if (!el.dataset.uid || el.dataset.uid === 'undefined') return; await M.showTab('player'); loadPlayer(el.dataset.uid).catch(fail); };

  // ==========================================================
  //  КЕЙСЫ / ИНВЕНТАРЬ / АПГРЕЙД / КОЛЛЕКЦИИ
  // ==========================================================
  panel('cases', head('Кейсы', 'Шансы каждого предмета и RTP кейса — на виду') + `
    <div class="tabs" id="cases-tabs">
      <button class="chip active" data-action="cases-tab" data-t="cases">Кейсы</button><button class="chip" data-action="cases-tab" data-t="inv">Инвентарь</button>
      <button class="chip" data-action="cases-tab" data-t="up">Апгрейд</button><button class="chip" data-action="cases-tab" data-t="coll">Коллекции</button></div>
    <div id="cases-body"></div>`);
  const C = { tab: 'cases', cases: [], recent: [], inv: [], sel: new Set(), targets: null, target: null, srcSum: 0, busy: false, open: null };
  const skinImg = (i) => `<img src="${esc(i.image_url)}" alt="" loading="lazy">`;

  M.tabs.cases = async () => { await casesTab(C.tab); };
  async function casesTab(t) {
    C.tab = t; $$('#cases-tabs .chip').forEach((c) => c.classList.toggle('active', c.dataset.t === t));
    const body = $('cases-body'); body.innerHTML = loading;
    try {
      if (t === 'cases') await renderCasesList();
      if (t === 'inv') await renderInv();
      if (t === 'up') await renderUp();
      if (t === 'coll') await renderColl();
    } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  M.actions['cases-tab'] = (el) => casesTab(el.dataset.t);

  async function renderCasesList() {
    const d = await api('/api/v2/cases'); C.cases = d.cases; C.recent = d.recent;
    $('cases-body').innerHTML = (d.recent.length ? `<div class="label">Последние дропы</div><div class="recent">${d.recent.map((r) => `<div class="it rar-${esc(r.rarity)}" title="${esc(r.user)}">${skinImg(r)}<div class="n">${esc(r.name)}</div><div class="p">${fmt(r.price)}</div></div>`).join('')}</div>` : '') +
      `<div class="grid-2">${d.cases.map((c) => `<div class="case-card" data-action="case-view" data-id="${c.id}"><div class="case-img"><img src="${esc(c.image_url)}" alt=""></div><div class="case-name">${esc(c.name)}</div><div class="case-price">${fmt(c.price)} PTS</div><div class="rtp-tag">RTP ${c.rtp}%</div></div>`).join('')}</div>
      <div class="muted-note" style="margin-top:12px">Шанс на экране = реальный шанс. RTP — средний возврат кейса игрокам (в стоимости предметов).</div>`;
  }
  function sheet(html) {
    let m = $('msp-sheet');
    if (!m) { m = document.createElement('div'); m.id = 'msp-sheet'; m.className = 'modal hidden'; m.dataset.action = 'sheet-backdrop'; document.body.appendChild(m); }
    m.innerHTML = `<div class="sheet tall">${html}</div>`; m.classList.remove('hidden'); return m;
  }
  const closeSheet = () => { const m = $('msp-sheet'); if (m) m.classList.add('hidden'); };
  M.actions['sheet-backdrop'] = (el, e) => { if (e.target === el && !C.busy) closeSheet(); };
  M.actions['sheet-close'] = () => { if (!C.busy) closeSheet(); };

  function caseSheet(c) {
    C.open = c;
    const items = [...c.items].sort((a, b) => b.price - a.price);
    sheet(`<div class="sheet-head"><div class="sheet-title">${esc(c.name)}</div><button class="icon-btn" data-action="sheet-close" aria-label="Закрыть"><svg class="ico"><use href="#i-x"/></svg></button></div>
      <div class="muted-note" style="margin-bottom:10px">${esc(c.desc)}<br><b class="win">RTP ${c.rtp}%</b> · ${items.length} предметов</div>
      <div id="case-stage"><div class="roulette hidden" id="c-roulette"><div class="roulette-track" id="c-strip"></div><div class="roulette-pointer"></div></div><div id="c-result"></div></div>
      <button class="btn btn-primary btn-block btn-lg" id="c-open" data-action="case-open" data-id="${c.id}">Открыть за ${fmt(c.price)} PTS</button>
      <div class="sub-head">Что внутри</div>
      <div class="items-grid">${items.map((i) => `<div class="it rar-${i.rarity}">${skinImg(i)}<div class="n">${esc(i.name)}</div><div class="p">${fmt(i.price)}</div><div class="c">${i.chance < 0.01 ? i.chance.toFixed(4) : i.chance.toFixed(2)}%</div></div>`).join('')}</div>`);
  }
  M.actions['case-view'] = (el) => { const c = C.cases.find((x) => x.id === Number(el.dataset.id)); if (c) caseSheet(c); };
  M.actions['case-open'] = async (el) => {
    if (C.busy) return; const c = C.cases.find((x) => x.id === Number(el.dataset.id)); if (!c) return;
    C.busy = true; const btn = $('c-open'); btn.disabled = true; $('c-result').innerHTML = '';
    try {
      const d = await post('/api/v2/cases/open', { case_id: c.id });
      const roul = $('c-roulette'), strip = $('c-strip'); roul.classList.remove('hidden');
      const pick = () => { let r = Math.random() * 100, a = 0; for (const i of c.items) { a += i.chance; if (r < a) return i; } return c.items[0]; };
      const WIN = 32, ITEM = 84, GAP = 10, arr = [];
      for (let i = 0; i < WIN; i++) arr.push(pick()); arr.push(d.skin); for (let i = 0; i < 6; i++) arr.push(pick());
      strip.innerHTML = arr.map((i) => `<div class="r-item rar-${esc(i.rarity)}"><img src="${esc(i.image_url)}" alt=""></div>`).join('');
      strip.style.transition = 'none'; strip.style.transform = 'translateX(0)'; void strip.offsetWidth;
      const view = roul.clientWidth, off = WIN * (ITEM + GAP) + ITEM / 2 - view / 2 + (Math.random() - 0.5) * 40;
      await sleep(60); strip.style.transition = 'transform 4.4s cubic-bezier(.12,.7,.12,1)'; strip.style.transform = `translateX(-${off}px)`; haptic('medium');
      await sleep(4600); roul.classList.add('hidden');
      U.setBal(d.balance); U.after(d);
      $('c-result').innerHTML = `<div class="drop-card rar-${esc(d.skin.rarity)}"><div class="tag">${esc(d.skin.rarity)} · шанс ${d.skin.chance}%</div>${skinImg(d.skin)}<div class="n">${esc(d.skin.name)}</div><div class="p">${fmt(d.skin.price)} PTS</div>
        <div class="btn-row"><button class="btn btn-ghost" data-action="sheet-sell" data-inv="${d.skin.inv_id}" data-p="${d.skin.price}">Продать ${fmt(d.skin.price)}</button><button class="btn btn-ghost" data-action="sheet-up" data-inv="${d.skin.inv_id}">Апгрейд</button></div></div>`;
      haptic('success');
    } catch (e) { fail(e); }
    finally { C.busy = false; btn.disabled = false; btn.textContent = `Открыть ещё за ${fmt(c.price)} PTS`; }
  };
  M.actions['sheet-sell'] = async (el) => {
    try { const r = await post('/api/v2/inventory/sell', { inv_ids: [Number(el.dataset.inv)] }); ok(r.message); U.setBal(r.balance); el.disabled = true; el.textContent = 'Продано ✓'; } catch (e) { fail(e); }
  };
  M.actions['sheet-up'] = async (el) => { C.sel = new Set([Number(el.dataset.inv)]); C.targets = null; C.target = null; closeSheet(); await casesTab('up'); };

  async function loadInv() { C.inv = await api('/api/v2/inventory'); for (const id of [...C.sel]) if (!C.inv.find((i) => i.inv_id === id)) C.sel.delete(id); }
  async function renderInv() {
    await loadInv();
    const total = C.inv.reduce((a, i) => a + i.price, 0);
    $('cases-body').innerHTML = C.inv.length ? `<div class="muted-note" style="margin-bottom:10px">${C.inv.length} предметов · стоимость ${fmt(total)} PTS · нажми, чтобы выбрать</div>
      <div class="items-grid">${C.inv.map((i) => `<div class="it rar-${esc(i.rarity)} ${C.sel.has(i.inv_id) ? 'sel' : ''}" data-action="inv-toggle" data-id="${i.inv_id}">${C.sel.has(i.inv_id) ? '<span class="ck-mark">✅</span>' : ''}${skinImg(i)}<div class="n">${esc(i.name)}</div><div class="p">${fmt(i.price)}</div></div>`).join('')}</div>
      <div class="btn-row" style="position:sticky;bottom:calc(var(--nav-h) + 14px);">
        <button class="btn btn-danger" data-action="inv-sell" ${C.sel.size ? '' : 'disabled'}>Продать (${C.sel.size}) · ${fmt(C.inv.filter((i) => C.sel.has(i.inv_id)).reduce((a, i) => a + i.price, 0))}</button>
        <button class="btn btn-warn" data-action="inv-to-up" ${C.sel.size ? '' : 'disabled'}>Апгрейд</button></div>
      <button class="btn btn-ghost btn-block" style="margin-top:8px" data-action="inv-all">${C.sel.size === C.inv.length ? 'Снять выбор' : 'Выбрать все'}</button>` : '<div class="empty">Инвентарь пуст.<br>Открой кейс!</div>';
  }
  M.actions['inv-toggle'] = (el) => { const id = Number(el.dataset.id); C.sel.has(id) ? C.sel.delete(id) : C.sel.add(id); C.targets = null; haptic('light'); renderInv(); };
  M.actions['inv-all'] = () => { if (C.sel.size === C.inv.length) C.sel.clear(); else C.inv.forEach((i) => C.sel.add(i.inv_id)); C.targets = null; renderInv(); };
  M.actions['inv-sell'] = async () => {
    const items = C.inv.filter((i) => C.sel.has(i.inv_id)); if (!items.length) return;
    const sum = items.reduce((a, i) => a + i.price, 0);
    if (!(await dialog({ title: 'Продать предметы?', text: `${items.length} шт. за ${fmt(sum)} PTS`, okText: 'Продать', danger: true }))) return;
    try { const r = await post('/api/v2/inventory/sell', { inv_ids: items.map((i) => i.inv_id) }); ok(r.message); U.setBal(r.balance); C.sel.clear(); renderInv(); } catch (e) { fail(e); }
  };
  M.actions['inv-to-up'] = () => { if (C.sel.size > 5) return toast('Для апгрейда максимум 5 предметов', 'err'); C.targets = null; casesTab('up'); };

  async function renderUp() {
    await loadInv();
    const src = C.inv.filter((i) => C.sel.has(i.inv_id)).slice(0, 5);
    if (!src.length) { $('cases-body').innerHTML = '<div class="empty">Выбери скины во вкладке «Инвентарь»<br>(до 5 штук), затем жми «Апгрейд».</div><button class="btn btn-primary btn-block" data-action="cases-tab" data-t="inv">В инвентарь</button>'; return; }
    const t = await api('/api/v2/upgrade/targets?inv_ids=' + src.map((i) => i.inv_id).join(','));
    C.targets = t.targets; C.srcSum = t.source_sum; if (!C.targets.find((x) => x.id === (C.target && C.target.id))) C.target = null;
    $('cases-body').innerHTML = `<div class="gcard">
      <div class="up-row"><div>${src.map((i) => `<div class="it rar-${esc(i.rarity)}" style="margin-bottom:6px;width:92px">${skinImg(i)}<div class="p">${fmt(i.price)}</div></div>`).join('')}</div>
        <div style="font-size:26px">➜</div><div id="up-target-box">${upTargetHtml()}</div></div>
      <div class="gauge" id="gauge" style="--ch:${C.target ? C.target.chance : 0}"><div class="needle" id="needle"></div><div class="mid"><b id="g-ch">${C.target ? C.target.chance + '%' : '—'}</b><small class="muted-note" style="border:0;background:none">шанс успеха</small></div></div>
      <button class="btn btn-primary btn-block btn-lg" id="up-go" data-action="up-go" ${C.target ? '' : 'disabled'}>Улучшить</button>
      <div id="up-res" class="res-pop"></div>
      <div class="rtp-note">Сумма: ${fmt(C.srcSum)} PTS · шанс = 95% × цена_источника ÷ цена_цели (комиссия 5%). Шанс на экране равен реальному.</div></div>
      <div class="sub-head">Выбери цель</div>
      <div class="target-list">${C.targets.map((x) => `<button class="target rar-${esc(x.rarity)} ${C.target && C.target.id === x.id ? 'sel' : ''}" data-action="up-pick" data-id="${x.id}"><img src="${esc(x.image_url)}" alt=""><div class="t1">${esc(x.name)}<small>${fmt(x.price)} PTS · x${x.mult}</small></div><div class="ch">${x.chance}%</div></button>`).join('') || '<div class="empty">Нет подходящих целей для этой суммы</div>'}</div>`;
  }
  const upTargetHtml = () => C.target ? `<div class="it rar-${esc(C.target.rarity)}" style="width:92px">${skinImg(C.target)}<div class="p">${fmt(C.target.price)}</div></div>` : '<div class="it" style="width:92px;aspect-ratio:1;display:grid;place-items:center;font-size:30px">?</div>';
  M.actions['up-pick'] = (el) => {
    if (C.busy) return; C.target = C.targets.find((x) => x.id === Number(el.dataset.id)); haptic('light');
    $$('.target').forEach((b) => b.classList.toggle('sel', b === el));
    $('up-target-box').innerHTML = upTargetHtml(); $('gauge').style.setProperty('--ch', C.target.chance); $('g-ch').textContent = C.target.chance + '%';
    $('up-go').disabled = false; $('needle').style.transition = 'none'; $('needle').style.transform = 'rotate(0)'; $('up-res').textContent = '';
  };
  M.actions['up-go'] = async () => {
    if (C.busy || !C.target) return; C.busy = true; const btn = $('up-go'); btn.disabled = true; const res = $('up-res'); res.textContent = '';
    try {
      const d = await post('/api/v2/upgrade', { inv_ids: [...C.sel].slice(0, 5), target_id: C.target.id });
      const n = $('needle'); n.style.transition = 'none'; n.style.transform = 'rotate(0)'; void n.offsetWidth;
      n.style.transition = 'transform 3.6s cubic-bezier(.1,.7,.05,1)'; n.style.transform = `rotate(${360 * 4 + d.roll * 3.6}deg)`; haptic('medium');
      await sleep(3800);
      res.className = 'res-pop ' + (d.success ? 'win' : 'lose'); res.textContent = d.success ? `УСПЕХ! ${d.target.name} (выпало ${d.roll}%)` : `Неудача: выпало ${d.roll}% (нужно < ${d.chance}%)`;
      haptic(d.success ? 'success' : 'error'); if (d.unlocked && d.unlocked.length) U.popups(d.unlocked); U.touchMe();
      C.sel.clear(); C.target = null; await sleep(1800); if (C.tab === 'up') await casesTab('up');
    } catch (e) { fail(e); btn.disabled = false; }
    finally { C.busy = false; }
  };

  async function renderColl() {
    const d = await api('/api/v2/collections');
    $('cases-body').innerHTML = d.map((c) => `<div class="coll"><div class="coll-head"><img src="${esc(c.image_url)}" alt=""><div style="flex:1"><b>${esc(c.name)}</b><div class="bar" style="margin-top:6px"><i style="width:${c.owned / c.total * 100}%"></i></div></div><b>${c.owned}/${c.total}</b></div>
      <div class="items-grid" style="grid-template-columns:repeat(5,1fr)">${c.items.map((i) => `<div class="it rar-${esc(i.rarity)} ${i.owned ? '' : 'owned-no'}" style="padding:3px">${skinImg(i)}</div>`).join('')}</div>
      <button class="btn btn-block ${c.complete && !c.claimed ? 'btn-success' : 'btn-ghost'}" style="margin-top:10px" data-action="coll-claim" data-id="${c.case_id}" ${c.complete && !c.claimed ? '' : 'disabled'}>${c.claimed ? 'Награда получена ✓' : c.complete ? `Забрать награду +${fmt(c.reward)} PTS` : `Награда за сет: ${fmt(c.reward)} PTS`}</button></div>`).join('') + '<div class="muted-note">Коллекция считается собранной, если ты когда-либо получал каждый предмет кейса.</div>';
  }
  M.actions['coll-claim'] = async (el) => { try { const r = await post('/api/v2/collections/claim', { case_id: Number(el.dataset.id) }); ok(`Коллекция собрана! +${fmt(r.reward)} PTS`); U.after(r); renderColl(); } catch (e) { fail(e); } };

  // ==========================================================
  //  АДМИН: автозагрузка матчей
  // ==========================================================
  const adminPanel = $('admin');
  if (adminPanel) {
    adminPanel.querySelector('.panel-head').insertAdjacentHTML('afterend', `<details class="acc" open><summary>Автозагрузка матчей и кэфов</summary><div class="acc-body"><div id="feed-status" class="admin-feed">Загрузка…</div>
      <button class="btn btn-primary" type="button" data-action="feed-sync">Синхронизировать сейчас</button></div></details>`);
  }
  async function feedStatus() {
    try {
      const s = await api('/api/v2/admin/feed/status');
      $('feed-status').innerHTML = `Статус: <b class="${s.enabled ? 'win' : 'lose'}">${s.enabled ? 'включена' : 'выключена'}</b> · открытых матчей из фида: <b>${s.open_events}</b><br>
        Последняя синхронизация: ${s.last_sync ? dt(s.last_sync) : '—'} (новых ${s.last_added}, обновлено кэфов ${s.last_updated})<br>Последний авто-расчёт: ${s.last_settle ? dt(s.last_settle) : '—'} (всего рассчитано ${s.total_settled})<br>
        Маржа ${(s.margin * 100).toFixed(0)}% · обновление каждые ${s.sync_minutes} мин · PandaScore (Dota/CS2): <b>${s.panda ? 'подключён' : 'нет токена'}</b><br>
        Лиги: ${s.leagues.map(esc).join(', ')}${s.errors.length ? `<br><span class="lose">Ошибки: ${s.errors.map(esc).join('; ')}</span>` : ''}`;
    } catch (e) { $('feed-status').textContent = e.message; }
  }
  M.tabs.admin = feedStatus;
  M.actions['feed-sync'] = async () => { try { toast('Синхронизация…'); const r = await post('/api/v2/admin/feed/sync'); ok(`Новых: ${r.new}, кэфов обновлено: ${r.updated}, рассчитано: ${r.settled}`); await feedStatus(); await M.loadEvents(); } catch (e) { fail(e); } };
})();
