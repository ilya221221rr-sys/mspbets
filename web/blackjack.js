/* ==========================================================
   MSPBETS — Блэкджек (21). Клиент только рисует: карты, колоду и выплаты решает сервер.
   Скрытая карта дилера до конца раздачи приходит как null — клиент её значение не знает.
   ========================================================== */
(() => {
  'use strict';
  const M = window.MSP;
  if (!M || !M.U) return;
  const { api, post, ok, fail, fmt, haptic, sleep, U } = M;
  const $ = (id) => document.getElementById(id);
  const main = document.querySelector('main.main');
  const anchor = $('history');

  const sec = document.createElement('section');
  sec.id = 'blackjack'; sec.className = 'panel hidden';
  sec.innerHTML = `<button class="back" type="button" data-tab="games">← Игры</button>
    <div class="panel-head"><h2>Блэкджек</h2><p>Набери 21 или обыграй дилера · блэкджек платит 3:2 · RTP ≈ 99%</p></div>
    <div class="gcard bj-table">
      <div class="bj-row"><div class="bj-label">Дилер <b id="bj-dt"></b></div><div class="bj-hand" id="bj-dealer"></div></div>
      <div class="res-pop" id="bj-res"></div>
      <div class="bj-row"><div class="bj-label">Вы <b id="bj-pt"></b></div><div class="bj-hand" id="bj-player"></div></div>
      <div id="bj-actions" class="bj-actions hidden">
        <button class="btn btn-primary btn-lg" type="button" data-action="bj-hit">Ещё</button>
        <button class="btn btn-warn btn-lg" type="button" data-action="bj-stand">Хватит</button>
        <button class="btn btn-ghost btn-lg" type="button" id="bj-double" data-action="bj-double">Удвоить</button>
      </div>
      <div id="bj-setup">${U.betBox('bj-bet', 100)}
        <button class="btn btn-primary btn-block btn-lg" type="button" style="margin-top:12px" data-action="bj-start">Раздать карты</button></div>
      <div class="rtp-note">6 колод · дилер стоит на 17 · блэкджек 3:2 · удвоение на первых двух картах · без сплита и страховки</div>
    </div>`;
  main.insertBefore(sec, anchor);

  const RANK = ['', 'A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'];
  const SUIT = ['♠', '♥', '♦', '♣'];
  const card = (c, i) => c
    ? `<div class="pcard ${c[1] === 1 || c[1] === 2 ? 'red' : ''}" style="animation-delay:${i * 70}ms"><span>${RANK[c[0]]}</span><i>${SUIT[c[1]]}</i></div>`
    : `<div class="pcard back" style="animation-delay:${i * 70}ms"></div>`;
  const total = (hand) => {
    let t = hand.reduce((a, c) => a + Math.min(c[0], 10), 0); const aces = hand.some((c) => c[0] === 1);
    return aces && t + 10 <= 21 ? t + 10 : t;
  };

  const BJ = { st: null, busy: false, over: null };
  const MSG = {
    blackjack: ['БЛЭКДЖЕК! 🎉', 'win'], win: ['Победа!', 'win'], push: ['Ничья — ставка возвращена', ''],
    lose: ['Дилер выиграл', 'lose'], bust: ['Перебор!', 'lose'],
  };

  function draw(st, over) {
    const sh = !!st;
    $('bj-setup').classList.toggle('hidden', sh);
    $('bj-actions').classList.toggle('hidden', !sh);
    if (sh) {
      $('bj-player').innerHTML = st.player.map(card).join('');
      $('bj-dealer').innerHTML = st.dealer.map(card).join('');
      $('bj-pt').textContent = (st.player_soft ? 'мягкая ' : '') + st.player_total;
      $('bj-dt').textContent = '';
      $('bj-double').disabled = !st.can_double || BJ.busy;
      document.querySelectorAll('#bj-actions .btn').forEach((b) => { if (b.id !== 'bj-double') b.disabled = BJ.busy; });
    } else if (over) {
      $('bj-player').innerHTML = over.player.map(card).join('');
      $('bj-dealer').innerHTML = over.dealer.map(card).join('');
      $('bj-pt').textContent = over.player_total; $('bj-dt').textContent = over.dealer_total;
    } else {
      $('bj-player').innerHTML = ''; $('bj-dealer').innerHTML = ''; $('bj-pt').textContent = ''; $('bj-dt').textContent = '';
    }
  }
  function finish(r) {
    BJ.st = null; BJ.over = r; draw(null, r);
    const [txt, cls] = MSG[r.outcome] || ['', ''];
    const res = $('bj-res'); res.className = 'res-pop ' + cls;
    res.textContent = r.payout > r.bet ? `${txt} +${fmt(r.payout - r.bet)} PTS` : r.outcome === 'push' ? txt : `${txt} −${fmt(r.bet)} PTS`;
    haptic(cls === 'win' ? 'success' : cls === 'lose' ? 'error' : 'medium');
    U.after(r); if (r.unlocked && r.unlocked.length) U.popups(r.unlocked);
  }
  async function act(path, body) {
    if (BJ.busy) return; BJ.busy = true; if (path === 'start') BJ.over = null; if (BJ.st) draw(BJ.st);
    try {
      const r = await post('/api/v2/games/blackjack/' + path, body);
      if (r.done) { if (r.state) draw(r.state); finish(r); }
      else { BJ.st = r.state; $('bj-res').textContent = ''; $('bj-res').className = 'res-pop'; if (r.balance != null) U.after(r); haptic('light'); }
    } catch (e) {
      fail(e);
      if (/активной/.test(e.message)) BJ.st = null;
      if (/Сначала завершите/.test(e.message)) { const d = await api('/api/v2/games/blackjack/state').catch(() => null); BJ.st = d && d.active ? d : null; }
    } finally { BJ.busy = false; draw(BJ.st, BJ.over); }
  }

  M.tabs.blackjack = async () => {
    const d = await api('/api/v2/games/blackjack/state');
    BJ.st = d.active ? d : null; BJ.over = null;
    $('bj-res').textContent = ''; $('bj-res').className = 'res-pop';
    draw(BJ.st);
  };
  M.actions['bj-start'] = async () => { try { act('start', { bet: U.bet('bj-bet') }); } catch (e) { fail(e); } };
  M.actions['bj-hit'] = () => act('hit');
  M.actions['bj-stand'] = () => act('stand');
  M.actions['bj-double'] = () => act('double');
})();
