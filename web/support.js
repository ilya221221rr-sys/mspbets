/* ==========================================================
   MSPBETS — поддержка (ИИ-помощник → человек, фото).
   Игрок: Профиль → Поддержка. Сначала отвечает ИИ, кнопка «👤 Позвать человека» подключает админа.
   Админ: Админ-панель → «💬 Поддержка» (список, переписка, ответ с фото, закрытие, «🤖 Вернуть ИИ»).
   Бэкенд: extras/support.py. Использует мост window.MSP (из app.js).
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M) return;
  const { api, post, ok, fail, esc, haptic } = M;
  const $ = (id) => document.getElementById(id);
  const main = document.querySelector('main.main');
  const dt = (iso) => { try { return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const nl = (t) => esc(t).replace(/\n/g, '<br>');

  // ---------- Фото: сжатие в браузере, показ с авторизацией ----------
  const MAX_SIDE = 1280;
  const MAX_BYTES = 1_200_000;

  // Файл -> data:image/jpeg;base64 (до MAX_SIDE по большей стороне, качество подбирается под MAX_BYTES)
  function compress(file) {
    return new Promise((resolve, reject) => {
      if (!file || !/^image\//.test(file.type)) return reject(new Error('Выберите картинку (JPG, PNG, WEBP)'));
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        const k = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * k)), h = Math.max(1, Math.round(img.height * k));
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        const ctx = c.getContext('2d');
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        let q = 0.85, out = c.toDataURL('image/jpeg', q);
        while (out.length * 0.75 > MAX_BYTES && q > 0.35) { q -= 0.1; out = c.toDataURL('image/jpeg', q); }
        if (out.length * 0.75 > 1_450_000) return reject(new Error('Фото слишком большое'));
        resolve(out);
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Не удалось открыть фото. Попробуйте JPG или PNG.')); };
      img.src = url;
    });
  }

  const blobCache = new Map();
  async function fileUrl(fid) {
    if (blobCache.has(fid)) return blobCache.get(fid);
    const res = await fetch('/api/v2/support/file/' + fid, { headers: { 'X-Telegram-Init-Data': (M.tg && M.tg.initData) || '' } });
    if (!res.ok) throw new Error('нет фото');
    const url = URL.createObjectURL(await res.blob());
    blobCache.set(fid, url);
    return url;
  }
  function hydrate(root) {
    root.querySelectorAll('img[data-fid]:not([data-ok])').forEach((img) => {
      img.dataset.ok = '1';
      fileUrl(Number(img.dataset.fid)).then((u) => { img.src = u; }).catch(() => {
        const s = document.createElement('span'); s.className = 'sup-gone'; s.textContent = '📷 фото недоступно';
        img.replaceWith(s);
      });
    });
  }
  function zoom(src) {
    const o = document.createElement('div');
    o.className = 'sup-zoom';
    o.innerHTML = `<img src="${src}" alt="">`;
    o.addEventListener('click', () => o.remove());
    document.body.appendChild(o);
  }
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (t && t.classList && t.classList.contains('sup-img') && t.src) zoom(t.src);
  });

  // Блок выбора фото (кнопка 📎 + превью) — общий для игрока и админа
  function attachUI(prefix) {
    let data = '';
    const inp = $(prefix + '-file'), pv = $(prefix + '-preview'), btn = $(prefix + '-attach');
    const clear = () => { data = ''; inp.value = ''; pv.classList.add('hidden'); pv.innerHTML = ''; btn.classList.remove('on'); };
    btn.addEventListener('click', () => inp.click());
    inp.addEventListener('change', async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      try {
        btn.disabled = true;
        data = await compress(f);
        pv.innerHTML = `<img src="${data}" alt=""><button type="button" class="sup-x" aria-label="Убрать фото">✕</button>`;
        pv.classList.remove('hidden'); btn.classList.add('on');
        pv.querySelector('.sup-x').addEventListener('click', clear);
      } catch (e) { clear(); fail(e); }
      finally { btn.disabled = false; }
    });
    return { get: () => data, clear };
  }
  const attachHtml = (p) => `
    <div id="${p}-preview" class="sup-preview hidden"></div>
    <input id="${p}-file" type="file" accept="image/*" hidden>`;
  const attachBtn = (p) => `<button type="button" class="btn btn-ghost sup-attach" id="${p}-attach" title="Прикрепить фото" aria-label="Прикрепить фото">📎</button>`;

  // ---------- Пузыри сообщений ----------
  const LABEL = {
    user: { player: 'Вы', admin: '👤 Игрок' },
    ai: { player: '🤖 ИИ-помощник', admin: '🤖 ИИ-помощник' },
    admin: { player: '🛟 Поддержка', admin: 'Вы' },
  };
  function bubbles(list, viewer, emptyHtml) {
    if (!list.length) return emptyHtml || '<div class="empty">Пока нет сообщений.</div>';
    return list.map((m) => {
      if (m.from === 'system') return `<div class="sup-sys">${nl(m.text)}</div>`;
      const own = viewer === 'player' ? m.from === 'user' : m.from === 'admin';
      const cls = own ? 'own' : (m.from === 'ai' ? 'ai' : 'other');
      const who = (LABEL[m.from] || LABEL.user)[viewer];
      const img = m.file ? `<img class="sup-img" data-fid="${m.file}" alt="фото">` : '';
      return `<div class="sup-msg ${cls}"><div class="sup-who">${who}</div>${img}${m.text ? `<div>${nl(m.text)}</div>` : ''}<time>${esc(dt(m.at))}</time></div>`;
    }).join('');
  }

  // ---------- Игрок ----------
  const sec = document.createElement('section');
  sec.id = 'support'; sec.className = 'panel hidden';
  sec.innerHTML = `
    <button class="back" type="button" data-tab="profile">← Профиль</button>
    <div class="panel-head"><h2>Поддержка</h2><p>Сначала отвечает ИИ-помощник. Если он не справился — позовите человека.</p></div>
    <div class="gcard sup-card">
      <div id="sup-mode" class="sup-mode"></div>
      <div id="sup-thread" class="sup-thread"><div class="skeleton"></div></div>
      <button type="button" class="btn btn-ghost sup-human hidden" id="sup-human">👤 Позвать человека</button>
      <form id="sup-form" class="sup-form">
        ${attachHtml('sup')}
        <textarea id="sup-text" class="input" rows="3" maxlength="1500" placeholder="Напишите сообщение..."></textarea>
        <div class="row">
          ${attachBtn('sup')}
          <button class="btn btn-primary" type="submit" id="sup-send">Отправить</button>
        </div>
      </form>
    </div>`;
  main.insertBefore(sec, $('history'));
  const supAtt = attachUI('sup');

  const WELCOME = `<div class="sup-msg ai"><div class="sup-who">🤖 ИИ-помощник</div><div>Привет! Я помогу с вопросами по MSPBETS: ставки, кейсы, бонусы, рефералка. Можно прикрепить скриншот 📎. Если я не справлюсь — нажмите «Позвать человека».</div></div>`;
  let lastSig = '', curMode = 'ai', aiOn = true, hasOpen = false;

  function applyMode() {
    const label = $('sup-mode'), hb = $('sup-human');
    if (!hasOpen) { label.textContent = aiOn ? '🤖 Отвечает ИИ-помощник' : '👤 Отвечает поддержка'; }
    else label.textContent = curMode === 'ai' ? '🤖 Отвечает ИИ-помощник' : '👤 Ваш вопрос у специалиста — ответ придёт сюда и в бота';
    label.className = 'sup-mode ' + (curMode === 'ai' && aiOn ? 'ai' : 'human');
    hb.classList.toggle('hidden', !(hasOpen && curMode === 'ai'));
  }
  async function loadThread(silent) {
    const box = $('sup-thread');
    const r = await api('/api/v2/support/thread');
    curMode = r.mode; aiOn = r.ai; hasOpen = !!r.open_ticket;
    applyMode();
    const sig = r.messages.map((m) => m.id).join(',') + '|' + r.mode;
    if (silent && sig === lastSig) return;
    lastSig = sig;
    const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    box.innerHTML = bubbles(r.messages, 'player', aiOn ? WELCOME : '<div class="empty">Пока нет сообщений.<br>Напишите нам — мы ответим 👇</div>');
    hydrate(box);
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
  const inSupport = () => !sec.classList.contains('hidden');
  setInterval(() => { if (document.hidden) return; if (inSupport()) loadThread(true).catch(() => {}); }, 8000);

  // временный пузырь «ИИ печатает…» на время запроса
  function showTyping(text, img) {
    const box = $('sup-thread');
    const mine = `<div class="sup-msg own"><div class="sup-who">Вы</div>${img ? `<img class="sup-img" src="${img}" alt="">` : ''}${text ? `<div>${nl(text)}</div>` : ''}<time>сейчас</time></div>`;
    const typing = aiOn && curMode === 'ai' ? '<div class="sup-msg ai sup-typing"><div class="sup-who">🤖 ИИ-помощник</div><div class="dots"><i></i><i></i><i></i></div></div>' : '';
    box.insertAdjacentHTML('beforeend', mine + typing);
    box.scrollTop = box.scrollHeight;
  }

  $('sup-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const ta = $('sup-text'), btn = $('sup-send');
    const text = ta.value.trim(), image = supAtt.get();
    if (!text && !image) return;
    btn.disabled = true; btn.textContent = 'Отправка...';
    showTyping(text, image);
    ta.value = ''; supAtt.clear();
    try {
      const r = await post('/api/v2/support/send', { text, image });
      haptic('success');
      if (r.escalated) ok('Специалист подключён');
      else if (r.mode === 'human' && !r.reply) ok(r.message || 'Отправлено');
    } catch (err) { fail(err); ta.value = text; }
    finally { btn.disabled = false; btn.textContent = 'Отправить'; lastSig = ''; try { await loadThread(false); } catch (_) {} }
  });

  $('sup-human').addEventListener('click', async () => {
    const b = $('sup-human'); b.disabled = true;
    try { const r = await post('/api/v2/support/human', {}); ok(r.message || 'Готово'); haptic('success'); lastSig = ''; await loadThread(false); }
    catch (err) { fail(err); }
    finally { b.disabled = false; }
  });

  M.supportBadge = pollUnread;
  const prevOnMe = M.onMe;
  M.onMe = (me) => { if (prevOnMe) prevOnMe(me); };

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
          ${attachHtml('supa')}
          <textarea id="sup-admin-text" class="input" rows="3" maxlength="1500" placeholder="Ответ игроку (можно приложить фото)..."></textarea>
          <div class="row">
            ${attachBtn('supa')}
            <button class="btn btn-primary" type="submit">Ответить</button>
          </div>
          <div class="row">
            <button class="btn btn-ghost" type="button" data-action="sup-ai" id="sup-ai-btn">🤖 Вернуть ИИ</button>
            <button class="btn btn-ghost" type="button" data-action="sup-close">Закрыть обращение</button>
          </div>
        </form>
      </div>
    </div>`;
  const adminSec = $('admin');
  adminSec.insertBefore(adm, adminSec.querySelector('details.acc'));
  const admAtt = attachUI('supa');

  let curTicket = 0, curTicketMode = 'human';
  const modeIcon = (m) => (m === 'ai' ? '🤖' : '👤');
  async function loadAdminList() {
    const box = $('sup-admin-list');
    try {
      const r = await api('/api/v2/admin/support');
      $('sup-admin-count').textContent = r.unread_total ? String(r.unread_total) : '';
      box.innerHTML = r.items.length ? r.items.map((t) => `
        <button type="button" class="sup-row ${t.status}" data-action="sup-open" data-id="${t.id}">
          <span class="sup-dot ${t.unread ? 'on' : ''}"></span>
          <span class="sup-info"><b>${t.status === 'open' ? modeIcon(t.mode) + ' ' : ''}#${t.id} · ${esc(t.name)}${t.username ? ' @' + esc(t.username) : ''}</b>
            <small>${t.last_from === 'admin' ? 'Вы: ' : (t.last_from === 'ai' ? '🤖 ' : '')}${esc(t.last)}</small></span>
          <span class="sup-meta">${t.status === 'open' ? (t.unread ? t.unread + ' нов.' : (t.mode === 'ai' ? 'отвечает ИИ' : 'открыто')) : 'закрыто'}<br>${esc(dt(t.updated_at))}</span>
        </button>`).join('') : '<div class="empty">Обращений пока нет</div>';
    } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  let adminSig = '';
  async function openTicket(id, silent) {
    curTicket = id;
    const r = await api('/api/v2/admin/support/' + id);
    $('sup-admin-list').classList.add('hidden');
    $('sup-admin-thread').classList.remove('hidden');
    const t = r.ticket;
    curTicketMode = t.mode;
    $('sup-admin-head').innerHTML = `<b>#${t.id} · ${esc(t.name)}</b>${t.username ? ` <a href="https://t.me/${esc(t.username)}" target="_blank" rel="noopener">@${esc(t.username)}</a>` : ''} <small>ID ${t.user_id} · ${t.status === 'open' ? 'открыто' : 'закрыто'} · ${t.mode === 'ai' ? '🤖 отвечает ИИ' : '👤 у человека'}</small>`;
    $('sup-ai-btn').classList.toggle('hidden', t.mode === 'ai' || t.status !== 'open');
    const sig = r.messages.map((m) => m.id).join(',') + '|' + t.mode + t.status;
    if (silent && sig === adminSig) return;
    adminSig = sig;
    const box = $('sup-admin-msgs');
    box.innerHTML = bubbles(r.messages, 'admin');
    hydrate(box);
    if (!silent) box.scrollTop = box.scrollHeight;
  }

  M.actions['sup-open'] = (el) => { adminSig = ''; return openTicket(Number(el.dataset.id)).catch(fail); };
  M.actions['sup-back'] = () => { curTicket = 0; $('sup-admin-thread').classList.add('hidden'); $('sup-admin-list').classList.remove('hidden'); loadAdminList(); };
  M.actions['sup-close'] = async () => {
    if (!curTicket) return;
    try { const r = await post('/api/v2/admin/support/close', { ticket_id: curTicket }); ok(r.message || 'Закрыто'); M.actions['sup-back'](); } catch (e) { fail(e); }
  };
  M.actions['sup-ai'] = async () => {
    if (!curTicket) return;
    try { const r = await post('/api/v2/admin/support/mode', { ticket_id: curTicket, mode: 'ai' }); ok(r.message || 'Готово'); adminSig = ''; await openTicket(curTicket); } catch (e) { fail(e); }
  };
  $('sup-admin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const ta = $('sup-admin-text'); const text = ta.value.trim(), image = admAtt.get();
    if ((!text && !image) || !curTicket) return;
    try {
      const r = await post('/api/v2/admin/support/reply', { ticket_id: curTicket, text, image });
      ta.value = ''; admAtt.clear(); ok(r.message || 'Отправлено'); haptic('success'); adminSig = ''; await openTicket(curTicket);
    } catch (err) { fail(err); }
  });
  setInterval(() => {
    if (document.hidden || adminSec.classList.contains('hidden') || !adm.open) return;
    if (curTicket) openTicket(curTicket, true).catch(() => {}); else loadAdminList();
  }, 10000);
  adm.addEventListener('toggle', () => { if (adm.open && !curTicket) loadAdminList(); });

  const prevAdmin = M.tabs.admin;
  M.tabs.admin = async () => { loadAdminList(); if (prevAdmin) return prevAdmin(); };

  // при заходе в профиль — обновить красную цифру, и один раз после старта приложения
  const prevProfile = M.tabs.profile;
  M.tabs.profile = async () => { if (prevProfile) await prevProfile(); pollUnread(); };
  setTimeout(pollUnread, 2500);
})();
