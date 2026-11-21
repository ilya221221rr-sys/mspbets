/* ==========================================================
   MSPBETS — ИИ-аналитика матча.
   Кнопка «🤖 ИИ-анализ» на карточке матча открывает окно с советом:
   на кого поставить, уверенность, причины, риски и кнопка «Поставить».
   Использует мост window.MSP из app.js (подключается после app.js).
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M) return;
  const { post, toast, fail, esc, haptic } = M;
  const $ = (id) => document.getElementById(id);

  const cache = new Map();           // event_id -> { data, at }
  const CLIENT_TTL = 5 * 60 * 1000;  // повторное открытие в течение 5 минут — без запроса
  let current = null;                // id матча, для которого открыто окно
  let seq = 0;                       // защита от гонок: ответ старого запроса не затирает новый

  function ensureModal() {
    let m = $('ai-modal');
    if (m) return m;
    m = document.createElement('div');
    m.id = 'ai-modal';
    m.className = 'modal hidden';
    m.dataset.action = 'ai-backdrop';
    m.innerHTML = `
      <div class="sheet ai-sheet" role="dialog" aria-modal="true" aria-label="ИИ-анализ матча">
        <div class="sheet-head">
          <div class="sheet-title">🤖 ИИ-анализ матча</div>
          <button class="icon-btn" type="button" data-action="ai-close" aria-label="Закрыть"><svg class="ico"><use href="#i-x"/></svg></button>
        </div>
        <div id="ai-body"></div>
      </div>`;
    document.body.appendChild(m);
    return m;
  }

  const open = () => { ensureModal().classList.remove('hidden'); };
  const close = () => { current = null; seq++; const m = $('ai-modal'); if (m) m.classList.add('hidden'); };

  function loadingHtml(title) {
    return `
      <div class="ai-match">${esc(title)}</div>
      <div class="ai-loading">
        <div class="ai-spin"></div>
        <div>ИИ изучает коэффициенты и форму команд…<small>Обычно это занимает 3–10 секунд</small></div>
      </div>`;
  }

  function bars(d) {
    const keys = Object.keys(d.probs || {});
    const short = (k) => (k === 'X' ? 'Ничья' : String((d.labels || {})[k] || k).replace(/^П[12] — /, ''));
    return `<div class="ai-probs">${keys.map((k) => `
      <div class="ai-prob${d.pick === k ? ' pick' : ''}">
        <div class="ai-prob-top"><span>${esc(short(k))}</span><b>${d.probs[k]}%</b></div>
        <div class="ai-bar"><i style="width:${Math.max(3, Math.min(100, d.probs[k]))}%"></i></div>
        <small>кэф ${Number((d.odds || {})[k]).toFixed(2)}</small>
      </div>`).join('')}</div>`;
  }

  function resultHtml(d) {
    const has = d.pick && d.pick !== 'none';
    const conf = Math.max(0, Math.min(100, Number(d.confidence) || 0));
    const level = conf >= 65 ? 'high' : conf >= 50 ? 'mid' : 'low';
    const verdict = has
      ? `<div class="ai-pick">
           <small>ИИ советует</small>
           <b>${esc(d.pick_label)}</b>
           <span class="ai-odd">кэф ${Number(d.pick_odds).toFixed(2)}</span>
         </div>`
      : `<div class="ai-pick skip"><small>ИИ советует</small><b>Пропустить матч</b><span class="ai-odd">явного фаворита нет</span></div>`;
    return `
      <div class="ai-match">${esc(d.title)}${d.league ? `<small> · ${esc(d.league)}</small>` : ''}</div>
      ${verdict}
      <div class="ai-conf ${level}">
        <div class="ai-conf-top"><span>Уверенность</span><b>${conf}%</b></div>
        <div class="ai-bar"><i style="width:${conf}%"></i></div>
      </div>
      <div class="ai-headline">${esc(d.headline)}</div>
      <ul class="ai-reasons">${(d.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
      ${d.risks ? `<div class="ai-risk"><b>⚠️ Риск:</b> ${esc(d.risks)}</div>` : ''}
      <div class="ai-sub">Шансы без маржи букмекера (маржа ${esc(d.margin)}%)</div>
      ${bars(d)}
      ${has ? `<button type="button" class="btn btn-primary btn-block btn-lg ai-go" data-action="ai-bet" data-id="${d.event_id}" data-choice="${esc(d.pick)}">Поставить: ${esc(d.pick_label)}</button>` : ''}
      <div class="ai-foot">${d.source === 'stats' ? 'ИИ сейчас недоступен — показан расчёт по коэффициентам. ' : ''}${esc(d.disclaimer)}</div>`;
  }

  function errorHtml(title, msg) {
    return `
      <div class="ai-match">${esc(title)}</div>
      <div class="ai-error">${esc(msg)}</div>
      <button type="button" class="btn btn-ghost btn-block" data-action="ai-retry">Повторить</button>`;
  }

  async function analyze(id, force) {
    const ev = (M.state.events || []).find((e) => e.id === id);
    const title = ev ? ev.title : 'Матч';
    current = id;
    const my = ++seq;
    open();
    const body = $('ai-body');
    const hit = cache.get(id);
    if (!force && hit && Date.now() - hit.at < CLIENT_TTL) { body.innerHTML = resultHtml(hit.data); return; }
    body.innerHTML = loadingHtml(title);
    try {
      const data = await post('/api/v2/analytics/match', { event_id: id });
      if (my !== seq) return;
      cache.set(id, { data, at: Date.now() });
      body.innerHTML = resultHtml(data);
      haptic('light');
    } catch (e) {
      if (my !== seq) return;
      body.innerHTML = errorHtml(title, (e && e.message) || 'Не удалось получить анализ. Попробуйте позже.');
    }
  }

  M.actions['ai-analyze'] = (el) => { haptic('light'); analyze(Number(el.dataset.id)); };
  M.actions['ai-close'] = close;
  M.actions['ai-backdrop'] = (el, e) => { if (e.target === el) close(); };
  M.actions['ai-retry'] = () => { if (current) analyze(current, true); };
  M.actions['ai-bet'] = (el) => {
    const id = Number(el.dataset.id), choice = el.dataset.choice;
    close();
    // Нажимаем кнопку коэффициента — так работают и одиночная ставка, и экспресс.
    const btn = document.querySelector(`.odd[data-id="${id}"][data-choice="${choice}"]`);
    if (btn) btn.click(); else toast('Матч уже недоступен для ставок', 'err');
  };
})();
