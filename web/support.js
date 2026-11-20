/* ==========================================================
   MSPBETS — поддержка.
   Игрок: Профиль → Поддержка (чат с админом, ответы приходят и в бота).
   Админ: Админ-панель → «Поддержка» (список обращений, переписка, ответ, закрытие).
   Бэкенд: extras/support.py. Использует мост window.MSP.
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M) return;
  const { api, post, ok, fail, esc, haptic, state } = M;
  const $ = (id) => document.getElementById(id);
  const main = document.querySelector('main.main');
  const dt = (iso) => { try { return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const nl = (t) => esc(t).replace(/\n/g, '<br>');

  // ---------- Игрок ----------
  const sec = document.createElement('section');
  sec.id = 'support'; sec.className = 'panel hidden';
  sec.innerHTML = `
    <button class="back" type="button" data-tab="profile">← Профиль</button>
    <div class="panel-head"><h2>Поддержка</h2><p>Опишите вопрос или проблему — ответ придёт сюда и в бота.</p></div>
    <div class="gcard sup-card">
      <div id="sup-thread" class="sup-thread"><div class="skeleton"></div></div>
      <form id="sup-form" class="sup-form">
        <textarea id="sup-text" class="input" rows="3" maxlength="1500" placeholder="Напишите сообщение..." required></textarea>
        <button class="btn btn-primary" type="submit" id="sup-send">Отправить</button>
      </form>
    </div>`;
  main.insertBefore(sec, $('history'));

  function bubbles(list, mine) {
    if (!list.length) return '<div class="empty">Пока нет сообщений.<br>Напишите нам — мы ответим 👇</div>';
    return list.map((m) => {
      const own = m.from === mine;
      return `<div class="sup-msg ${own ? 'own' : 'other'}"><div class="sup-who">${own ? 'Вы' : (mine === 'user' ? '🛟 Поддержка' : '👤 Игрок')}</div>${nl(m.text)}<time>${esc(dt(m.at))}</time></div>`;
    }).join('');
  }

  let lastSig = '';
  async function loadThread(silent) {
    const box = $('sup-thread');
    const r = await api('/api/v2/support/thread');
    const sig = r.messages.map((m) => m.id).join(',');
    if (silent && sig === lastSig) return;
    lastSig = sig;
    const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    box.innerHTML = bubbles(r.messages, 'user');
    if (!silent || stick) box.scrollTop = box.scrollHeight;
    refreshBadge(0);
  }
  function refreshBadge(n) {
    const b = $('sup-badge');
    if (b) { b.textContent = n > 0 ? String(n) : ''; b.classList.toggle('on', n > 0); }
  }
  async function pollUnread() {
    try { const r = await api('/api/v2/support/unread'); refreshBadge(r.unread); return r.unread; } catch (_) { return 0; }
  }

  M.tabs.support = async () => { await loadThread(false); };
  let timer = 0;
  const inSupport = () => !sec.classList.contains('hidden');
  timer = setInterval(() => { if (document.hidden) return; if (inSupport()) loadThread(true).catch(() => {}); }, 8000);

  $('sup-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const ta = $('sup-text'), btn = $('sup-send');
    const text = ta.value.trim();
    if (!text) return;
    btn.disabled = true; btn.textContent = 'Отправка...';
    try {
      const r = await post('/api/v2/support/send', { text });
      ta.value = ''; haptic('success'); ok(r.message || 'Отправлено');
      await loadThread(false);
    } catch (err) { fail(err); }
    finally { btn.disabled = false; btn.textContent = 'Отправить'; }
  });

  // красная цифра на пункте «Поддержка» в профиле
  const prevOnMe = M.onMe;
  M.onMe = (me) => { if (prevOnMe) prevOnMe(me); };
  M.supportBadge = pollUnread;

  // ---------- Админ ----------
  const adm = document.createElement('details');
  adm.className = 'acc'; adm.id = 'sup-admin';
  adm.innerHTML = `
    <summary>💬 Поддержка (обращения игроков) <span id="sup-admin-count" class="sup-count"></span></summary>
    <div class="acc-body">
      <div id="sup-admin-list" class="sup-list"><div class="empty">Загрузка...</div></div>
      <div id="sup-admin-thread" class="hidden">
        <button class="back" type="button" data-action="sup-back">← К списку</button>
        <div id="sup-admin-head" class="sup-head"></div>
        <div id="sup-admin-msgs" class="sup-thread"></div>
        <form id="sup-admin-form" class="sup-form">
          <textarea id="sup-admin-text" class="input" rows="3" maxlength="1500" placeholder="Ответ игроку..." required></textarea>
          <div class="row">
            <button class="btn btn-primary" type="submit">Ответить</button>
            <button class="btn btn-ghost" type="button" data-action="sup-close">Закрыть обращение</button>
          </div>
        </form>
      </div>
    </div>`;
  const adminSec = $('admin');
  adminSec.insertBefore(adm, adminSec.querySelector('details.acc'));

  let curTicket = 0;
  async function loadAdminList() {
    const box = $('sup-admin-list');
    try {
      const r = await api('/api/v2/admin/support');
      $('sup-admin-count').textContent = r.unread_total ? String(r.unread_total) : '';
      box.innerHTML = r.items.length ? r.items.map((t) => `
        <button type="button" class="sup-row ${t.status}" data-action="sup-open" data-id="${t.id}">
          <span class="sup-dot ${t.unread ? 'on' : ''}"></span>
          <span class="sup-info"><b>#${t.id} · ${esc(t.name)}${t.username ? ' @' + esc(t.username) : ''}</b>
            <small>${t.last_from === 'admin' ? 'Вы: ' : ''}${esc(t.last)}</small></span>
          <span class="sup-meta">${t.status === 'open' ? (t.unread ? t.unread + ' нов.' : 'открыто') : 'закрыто'}<br>${esc(dt(t.updated_at))}</span>
        </button>`).join('') : '<div class="empty">Обращений пока нет</div>';
    } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  async function openTicket(id, silent) {
    curTicket = id;
    const r = await api('/api/v2/admin/support/' + id);
    $('sup-admin-list').classList.add('hidden');
    $('sup-admin-thread').classList.remove('hidden');
    const t = r.ticket;
    $('sup-admin-head').innerHTML = `<b>#${t.id} · ${esc(t.name)}</b>${t.username ? ` <a href="https://t.me/${esc(t.username)}" target="_blank" rel="noopener">@${esc(t.username)}</a>` : ''} <small>ID ${t.user_id} · ${t.status === 'open' ? 'открыто' : 'закрыто'}</small>`;
    const box = $('sup-admin-msgs');
    box.innerHTML = bubbles(r.messages, 'admin');
    if (!silent) box.scrollTop = box.scrollHeight;
  }

  M.actions['sup-open'] = (el) => openTicket(Number(el.dataset.id)).catch(fail);
  M.actions['sup-back'] = () => { curTicket = 0; $('sup-admin-thread').classList.add('hidden'); $('sup-admin-list').classList.remove('hidden'); loadAdminList(); };
  M.actions['sup-close'] = async () => {
    if (!curTicket) return;
    try { const r = await post('/api/v2/admin/support/close', { ticket_id: curTicket }); ok(r.message || 'Закрыто'); M.actions['sup-back'](); } catch (e) { fail(e); }
  };
  $('sup-admin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const ta = $('sup-admin-text'); const text = ta.value.trim();
    if (!text || !curTicket) return;
    try { const r = await post('/api/v2/admin/support/reply', { ticket_id: curTicket, text }); ta.value = ''; ok(r.message || 'Отправлено'); haptic('success'); await openTicket(curTicket); } catch (err) { fail(err); }
  });
  setInterval(() => {
    if (document.hidden || adminSec.classList.contains('hidden') || !adm.open) return;
    if (curTicket) openTicket(curTicket, true).catch(() => {}); else loadAdminList();
  }, 10000);
  adm.addEventListener('toggle', () => { if (adm.open && !curTicket) loadAdminList(); });

  const prevAdmin = M.tabs.admin;
  M.tabs.admin = async () => { loadAdminList(); if (prevAdmin) return prevAdmin(); };

  // при заходе в профиль — обновить красную цифру
  const prevProfile = M.tabs.profile;
  M.tabs.profile = async () => { if (prevProfile) await prevProfile(); pollUnread(); };
  // и один раз после старта приложения
  setTimeout(pollUnread, 2500);
})();
