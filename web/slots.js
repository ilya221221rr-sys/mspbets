/* ==========================================================
   MSPBETS — слоты (ТОЛЬКО ДЕМО-РЕЖИМ).
   Игры запускаются на серверах провайдеров с виртуальными монетами провайдера.
   Баллы PTS не списываются и не начисляются, XP и задания не засчитываются.
   Чтобы добавить слот — допишите строку в SLOTS ниже.
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M || !M.U) return;
  const { esc, haptic, openLink } = M;
  const $ = (id) => document.getElementById(id);
  const main = document.querySelector('main.main');
  const anchor = $('history');

  // p: 'pp' = Pragmatic Play (id = gameSymbol демо-сервера), 'hs' = Hacksaw Gaming (id = gameid)
  // [провайдер, id, название, эмодзи, цвет1, цвет2, метка]
  const SLOTS = [
    ['hs', '1830', 'Le Zeus', '⚡', '#FFB02E', '#7C3AED', 'NEW'],
    ['pp', 'vs20olympgate', 'Gates of Olympus', '⚡', '#7C5CFF', '#22D3EE', 'TOP'],
    ['pp', 'vs20fruitsw', 'Sweet Bonanza', '🍭', '#FF5DAE', '#FFC93C', 'TOP'],
    ['pp', 'vs20sugarrush', 'Sugar Rush', '🍬', '#FF5D7A', '#B26BFF'],
    ['pp', 'vs20starlight', 'Starlight Princess', '⭐', '#B26BFF', '#FF5DAE'],
    ['pp', 'vs20doghouse', 'The Dog House', '🐶', '#FFA43C', '#2EE59D'],
    ['pp', 'vs10bbbonanza', 'Big Bass Bonanza', '🎣', '#22D3EE', '#2E7BFF'],
    ['pp', 'vs20fruitparty', 'Fruit Party', '🍓', '#FF5D7A', '#FFC93C'],
    ['pp', 'vs20olympx', 'Gates of Olympus 1000', '🔱', '#5B3DFF', '#FFC93C', '1000'],
    ['pp', 'vs20sugarrushx', 'Sugar Rush 1000', '🍬', '#FF3D8B', '#7C5CFF', '1000'],
    ['pp', 'vs20starlightx', 'Starlight Princess 1000', '🌟', '#9B4DFF', '#FF86C8', '1000'],
    ['pp', 'vs20sbxmas', 'Sweet Bonanza Xmas', '🎄', '#2EE59D', '#FF5D7A'],
    ['pp', 'vs20fparty2', 'Fruit Party 2', '🍒', '#FF3D5E', '#FFA43C'],
    ['pp', 'vs12bbb', 'Bigger Bass Bonanza', '🐟', '#1FB6FF', '#0B5FFF'],
    ['pp', 'vs10txbigbass', 'Big Bass Splash', '💦', '#22D3EE', '#7C5CFF'],
    ['pp', 'vs40wildwest', 'Wild West Gold', '🤠', '#FFA43C', '#A0521D'],
    ['pp', 'vs25wolfgold', 'Wolf Gold', '🐺', '#FFC93C', '#3B4A7A'],
    ['pp', 'vswaysbufking', 'Buffalo King Megaways', '🦬', '#FF8A3C', '#7A3B1D'],
    ['pp', 'vswaysrhino', 'Great Rhino Megaways', '🦏', '#2EE59D', '#3B6A4A'],
    ['pp', 'vs25mustang', 'Mustang Gold', '🐎', '#FFC93C', '#B2561D'],
    ['pp', 'vs25chilli', 'Chilli Heat', '🌶️', '#FF3D3D', '#FFA43C'],
    ['pp', 'vs20aztecgates', 'Gates of Aztec', '🗿', '#2EE59D', '#FFC93C'],
    ['pp', 'vs20hercpeg', 'Hercules and Pegasus', '🏛️', '#6EA8FF', '#FFC93C'],
    ['pp', 'vs10bookoftut', 'Book of Tut', '📜', '#FFC93C', '#2E1D7A'],
    ['pp', 'vs20kraken', 'Release the Kraken', '🐙', '#22D3EE', '#5B3DFF'],
    ['pp', 'vs20chickdrop', 'Chicken Drop', '🐔', '#FFC93C', '#FF5D7A'],
    ['pp', 'vs10madame', 'Madame Destiny', '🔮', '#B26BFF', '#FF5DAE'],
    ['pp', 'vs10egrich', 'Queen of Gods', '👑', '#FFC93C', '#B26BFF'],
    ['pp', 'vs5spjoker', 'Super Joker', '🃏', '#FF5D7A', '#FFC93C'],
    ['hs', '1309', 'Le Bandit', '🦝', '#FFA43C', '#3B2A7A', 'HIT'],
    ['hs', '1562', 'Le Pharaoh', '🔺', '#FFC93C', '#2E7BFF'],
    ['hs', '1689', 'Le Viking', '🪓', '#6EA8FF', '#3B4A7A'],
    ['hs', '2057', 'Le Fisherman', '🎣', '#22D3EE', '#FFA43C'],
    ['hs', '1924', 'Le Cowboy', '🤠', '#FFA43C', '#A0521D'],
    ['hs', '1787', 'Le King', '👑', '#FFC93C', '#FF5D7A'],
    ['hs', '1965', 'Le Santa', '🎅', '#FF5D7A', '#2EE59D'],
    ['hs', '1067', 'Wanted Dead or a Wild', '🔫', '#FF8A3C', '#7A1D1D', 'HIT'],
    ['hs', '1059', 'Chaos Crew', '💀', '#2EE59D', '#B26BFF'],
    ['hs', '1352', 'Chaos Crew 2', '🎨', '#FF5DAE', '#22D3EE'],
    ['hs', '1456', 'Cash Crew', '💰', '#2EE59D', '#FFC93C'],
    ['hs', '1233', 'Rip City', '🏀', '#FF8A3C', '#2E1D7A'],
    ['hs', '1400', '2 Wild 2 Die', '🎸', '#FF3D5E', '#7C5CFF'],
  ];
  const PROV = { pp: 'Pragmatic Play', hs: 'Hacksaw Gaming' };
  const S = { prov: 'all', q: '' };

  main.insertBefore(Object.assign(document.createElement('section'), {
    id: 'slots', className: 'panel hidden',
    innerHTML: `<button class="back" type="button" data-tab="games">← Игры</button>
      <div class="panel-head"><h2>Слоты</h2><p>Демо-режим от известных провайдеров</p></div>
      <div class="slot-demo-note">🎰 Это <b>демо</b>: играете на виртуальные монеты провайдера. Баллы PTS не тратятся и не выигрываются.</div>
      <input id="slot-search" class="input" type="search" placeholder="Поиск слота…" autocomplete="off">
      <div class="slot-chips" id="slot-chips"></div>
      <div class="slot-grid" id="slot-grid"></div>`,
  }), anchor);

  const chip = (k, label) => `<button type="button" class="slot-chip ${S.prov === k ? 'on' : ''}" data-action="slot-prov" data-p="${k}">${label}</button>`;
  function render() {
    $('slot-chips').innerHTML = chip('all', 'Все') + chip('pp', 'Pragmatic Play') + chip('hs', 'Hacksaw');
    const q = S.q.trim().toLowerCase();
    const list = SLOTS.filter((s) => (S.prov === 'all' || s[0] === S.prov) && (!q || s[2].toLowerCase().includes(q)));
    $('slot-grid').innerHTML = list.map((s) => `
      <button type="button" class="slot-tile" data-action="slot-play" data-p="${s[0]}" data-id="${esc(s[1])}" style="--c1:${s[4]};--c2:${s[5]}">
        ${s[6] ? `<span class="slot-tag">${esc(s[6])}</span>` : ''}
        <span class="slot-emo">${s[3]}</span>
        <span class="slot-name">${esc(s[2])}</span>
        <span class="slot-prov">${PROV[s[0]]}</span>
        <span class="slot-play">▶ Демо</span>
      </button>`).join('') || '<div class="empty">Ничего не найдено</div>';
  }
  $('slot-search').addEventListener('input', (e) => { S.q = e.target.value; render(); });
  M.tabs.slots = render;

  // ---------- запуск ----------
  const urlFor = (p, id) => {
    const origin = location.origin;
    if (p === 'pp') {
      const q = new URLSearchParams({ gameSymbol: id, websiteUrl: origin, jurisdiction: '99', lobbyUrl: origin + '/web/slot-close.html', lang: 'ru', cur: 'USD' });
      return 'https://demogamesfree.pragmaticplay.net/gs2c/openGame.do?' + q.toString();
    }
    return origin + '/web/hacksaw.html?gameid=' + encodeURIComponent(id) + '&lang=ru';
  };

  let overlay = null;
  function close() {
    if (!overlay) return;
    const f = overlay.querySelector('iframe'); if (f) f.src = 'about:blank';
    overlay.remove(); overlay = null; document.documentElement.classList.remove('slot-open');
  }
  function open(p, id) {
    const s = SLOTS.find((x) => x[0] === p && x[1] === id); if (!s) return;
    close();
    const url = urlFor(p, id);
    overlay = document.createElement('div'); overlay.className = 'slot-ov';
    overlay.innerHTML = `
      <div class="slot-bar">
        <button type="button" class="slot-x" data-action="slot-close" aria-label="Закрыть">✕</button>
        <div class="slot-ttl"><b>${esc(s[2])}</b><small>${PROV[p]} · ДЕМО</small></div>
        <button type="button" class="slot-x" data-action="slot-ext" aria-label="Открыть в браузере" title="Открыть в браузере">↗</button>
      </div>
      <div class="slot-frame"><div class="slot-load">Загрузка слота…<br><small>Если не открывается — нажмите ↗ вверху</small></div>
        <iframe src="${esc(url)}" allow="autoplay; fullscreen; clipboard-write" allowfullscreen referrerpolicy="no-referrer-when-downgrade"></iframe></div>`;
    overlay.dataset.url = url;
    document.body.appendChild(overlay);
    document.documentElement.classList.add('slot-open');
    const fr = overlay.querySelector('iframe'), ld = overlay.querySelector('.slot-load');
    fr.addEventListener('load', () => { setTimeout(() => ld && ld.remove(), 600); });
    haptic && haptic('light');
  }

  M.actions['slot-prov'] = (el) => { S.prov = el.dataset.p; render(); };
  M.actions['slot-play'] = (el) => open(el.dataset.p, el.dataset.id);
  M.actions['slot-close'] = close;
  M.actions['slot-ext'] = () => { if (overlay) openLink(overlay.dataset.url); };
  window.addEventListener('message', (e) => { if (e.origin === location.origin && e.data && e.data.mspSlotClose) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  // уход со вкладки слотов всегда закрывает окно слота
  const prevLeave = M.leave;
  M.leave = (name) => { if (name !== 'slots') close(); if (prevLeave) prevLeave(name); };
})();
