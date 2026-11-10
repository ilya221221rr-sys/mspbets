const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const headers = {"Content-Type":"application/json","X-Telegram-Init-Data":tg?.initData || ""};
let me = null, events = [];
const $ = id => document.getElementById(id);

async function api(path, options={}) {
  const res = await fetch(path, {...options, headers:{...headers, ...(options.headers||{})}});
  const data = await res.json().catch(()=>({detail:"Ошибка"}));
  if (!res.ok) throw new Error(data.detail || "Ошибка");
  return data;
}
function toast(m) {
  const t = $("toast"); t.textContent = m; t.classList.remove("hidden");
  setTimeout(()=> t.classList.add("hidden"), 2500);
}

async function loadMe() {
  me = await api("/api/me");
  $("balance").textContent = me.balance.toLocaleString("ru-RU");
  if (me.is_admin) document.querySelectorAll(".admin-only").forEach(x=>x.classList.remove("hidden"));
}

async function loadEvents() {
  events = await api("/api/events");
  const open = events.filter(e=>e.status==="open");
  $("event-list").innerHTML = open.map(e=>{
    const hasDraw = e.sport === "Футбол";
    return `
    <article class="event" data-id="${e.id}">
      <div class="event-top"><span>${e.sport}</span><span>Кф: ${e.odds_1} ${hasDraw ? '/ '+e.odds_x : ''} / ${e.odds_2}</span></div>
      <h3>${e.title}</h3>
      <div class="choices">
        <button class="choice" onclick="placeBet(${e.id}, '1', ${e.odds_1})">П1 (${e.odds_1})</button>
        ${hasDraw ? `<button class="choice" onclick="placeBet(${e.id}, 'X', ${e.odds_x})">X (${e.odds_x})</button>` : ''}
        <button class="choice" onclick="placeBet(${e.id}, '2', ${e.odds_2})">П2 (${e.odds_2})</button>
      </div>
    </article>`;
  }).join("") || `<div class="empty">Нет матчей</div>`;
}

window.placeBet = async function(eventId, choice, odds) {
  const points = prompt(`Ставка на ${choice} с кф. ${odds}.\nВведите сумму:`, "100");
  if (!points) return;
  try {
    await api("/api/predict", { method: "POST", body: JSON.stringify({ event_id: eventId, points: Number(points), choice }) });
    toast(`Ставка принята по кэфу ${odds}!`);
    await loadMe();
  } catch(e) { toast(e.message); }
}

async function loadHistory() {
  const hist = await api("/api/history");
  $("history-list").innerHTML = hist.length ? hist.map(h => {
    let statusText = "В ожидании"; let statusColor = "#ccc";
    if (h.status === "won") { statusText = `Выиграл: +${Math.floor(h.points * h.locked_odds)} PTS`; statusColor = "#55ff00"; }
    if (h.status === "lost") { statusText = "Проиграл"; statusColor = "#f87171"; }
    return `
    <div class="history-card" style="border-left: 4px solid ${statusColor};">
      <div class="history-title">${h.title} (${h.sport})</div>
      <div style="color: #aaa; font-size: 12px; margin: 4px 0;">Исход: ${h.choice} | Кф: ${h.locked_odds} | Ставка: ${h.points} PTS</div>
      <div class="history-status" style="color: ${statusColor};">${statusText}</div>
    </div>`;
  }).join("") : `<div class="empty">История пуста</div>`;
}

async function loadRating() {
  const rows = await api("/api/rating");
  $("rating-list").innerHTML = rows.map((r,i)=>`
    <div class="rank" style="display: flex; justify-content: space-between; padding: 10px; background: #1a1a1a; margin-bottom: 5px; border-radius: 4px;">
      <span>${i+1}. ${r.first_name || r.username}</span><b>${r.balance} PTS</b>
    </div>`).join("") || `<div class="empty">Пусто</div>`;
}

// --- НОВОСТИ ---
async function loadNews() {
  const newsList = $("news-list");
  newsList.innerHTML = `<div class="empty">Загружаем новости...</div>`;
  try {
    const news = await api("/api/dota-news");
    if (!news || news.length === 0) {
      newsList.innerHTML = `<div class="empty">Не удалось загрузить новости.</div>`;
      return;
    }
    newsList.innerHTML = news.map(n => `
      <div onclick="openNewsDetail('${n.link}')" style="background: #1a1a1a; padding: 15px; border-radius: 8px; margin-bottom: 10px; border: 1px solid #333; cursor: pointer;">
        <div style="color: #55ff00; font-weight: bold; font-size: 14px; margin-bottom: 5px;">${n.title}</div>
        <div style="color: #888; font-size: 11px;">Нажмите, чтобы читать</div>
      </div>
    `).join("");
  } catch(e) {
    newsList.innerHTML = `<div class="empty">Ошибка: ${e.message}</div>`;
  }
}

window.openNewsDetail = async function(url) {
  const modal = $("news-modal");
  const content = $("news-modal-content");
  modal.classList.remove("hidden");
  content.innerHTML = `<div style="text-align: center; padding: 20px;">Загрузка...</div>`;
  try {
    const data = await api(`/api/news-detail?url=${encodeURIComponent(url)}`);
    content.innerHTML = data.html;
  } catch(e) {
    content.innerHTML = `<div style="color: #f87171;">Ошибка загрузки: ${e.message}</div>`;
  }
}

// --- КРАШ ---
let crashInterval = null, currentCrashPoint = 0, currentCrashMultiplier = 1.00, isCrashed = false;
window.startCrash = function() {
  const bet = Number($("crash-bet").value);
  if (bet < 1) return toast("Введите сумму");
  if (crashInterval) return;
  $("crash-start-btn").disabled = true; $("crash-cashout-btn").disabled = false;
  $("crash-status").textContent = "ИГРА ИДЕТ..."; $("crash-status").style.color = "#55ff00";
  $("crash-line").style.height = "0%"; currentCrashMultiplier = 1.00; isCrashed = false;
  const rand = Math.random();
  if (rand < 0.5) currentCrashPoint = 1 + Math.random() * 0.5;
  else currentCrashPoint = 1.5 + Math.random() * 10;
  
  crashInterval = setInterval(() => {
    if (isCrashed) return;
    currentCrashMultiplier += 0.05;
    const heightPerc = Math.min((currentCrashMultiplier / currentCrashPoint) * 100, 100);
    $("crash-line").style.height = `${heightPerc}%`;
    $("crash-mult").textContent = currentCrashMultiplier.toFixed(2) + "x";
    if (currentCrashMultiplier >= currentCrashPoint) crashEnd();
  }, 100);
}
window.cashoutCrash = async function() {
  if (isCrashed || currentCrashMultiplier <= 1.00) return;
  isCrashed = true; clearInterval(crashInterval); crashInterval = null;
  const bet = Number($("crash-bet").value);
  try {
    const res = await api("/api/crash/play", { method: "POST", body: JSON.stringify({ amount: bet, cashout: Number(currentCrashMultiplier) }) });
    toast(`Забрано на ${currentCrashMultiplier.toFixed(2)}x! Выигрыш: ${res.win_amount} PTS`);
    $("crash-status").textContent = `Забрано на ${currentCrashMultiplier.toFixed(2)}x`; $("crash-status").style.color = "#55ff00";
  } catch(e) { toast(e.message); }
  resetCrashButtons(); await loadMe();
}
function crashEnd() {
  isCrashed = true; clearInterval(crashInterval); crashInterval = null;
  $("crash-mult").textContent = currentCrashPoint.toFixed(2) + "x";
  $("crash-status").textContent = `КРАШ! Игра упала на ${currentCrashPoint.toFixed(2)}x`; $("crash-status").style.color = "#f87171";
  $("crash-line").style.background = "#f87171";
  const bet = Number($("crash-bet").value);
  api("/api/crash/play", { method: "POST", body: JSON.stringify({ amount: bet, cashout: 0 }) }).catch(()=>{});
  setTimeout(() => { $("crash-line").style.background = "linear-gradient(to right, rgba(85,255,0,0.2), #55ff00)"; }, 2000);
  resetCrashButtons(); loadMe();
}
function resetCrashButtons() { $("crash-start-btn").disabled = false; $("crash-cashout-btn").disabled = true; }

// --- КЕЙСЫ С АНИМАЦИЕЙ ---
async function loadCases() {
  const cases = await api("/api/cases");
  $("cases-list").innerHTML = cases.map(c => `
    <div style="background: #1a1a1a; padding: 15px; border-radius: 8px; text-align: center;">
      <img src="${c.image_url}" style="width: 100%; border-radius: 4px;">
      <div style="font-weight: bold; margin: 5px 0;">${c.name}</div>
      <div style="color: #55ff00; margin-bottom: 10px;">${c.price} PTS</div>
      <button onclick="openCase(${c.id})" class="primary">Открыть</button>
    </div>
  `).join("");
}

window.openCase = async function(caseId) {
  const strip = $("case-strip");
  const pointer = $("case-pointer");
  const resultText = $("case-result-text");
  
  resultText.style.display = "none";
  strip.style.display = "flex";
  pointer.style.display = "block";
  strip.innerHTML = "";
  
  try {
    const data = await api("/api/cases/open", { method: "POST", body: JSON.stringify({ case_id: caseId }) });
    if (!data.skin || !data.skin.image_url) throw new Error("Сервер не вернул скин");

    const fakeSkins = ["https://i.imgur.com/G7XpkYs.png", "https://i.imgur.com/0QWoP2i.png", "https://i.imgur.com/7x8YDiz.png", "https://i.imgur.com/BegS1KI.png"];
    let stripHTML = "";
    for (let i = 0; i < 30; i++) {
      const randomImg = fakeSkins[Math.floor(Math.random() * fakeSkins.length)];
      stripHTML += `<img src="${randomImg}" style="width: 80px; height: 80px; border-radius: 4px; border: 1px solid #333; flex-shrink: 0;">`;
    }
    stripHTML += `<img src="${data.skin.image_url}" style="width: 80px; height: 80px; border-radius: 4px; border: 2px solid #55ff00; box-shadow: 0 0 10px #55ff00; flex-shrink: 0;">`;
    for (let i = 0; i < 5; i++) {
      const randomImg = fakeSkins[Math.floor(Math.random() * fakeSkins.length)];
      stripHTML += `<img src="${randomImg}" style="width: 80px; height: 80px; border-radius: 4px; border: 1px solid #333; flex-shrink: 0;">`;
    }
    
    strip.innerHTML = stripHTML;
    strip.style.transition = "none";
    strip.style.transform = `translateX(0)`;
    
    setTimeout(() => {
      const screenWidth = window.innerWidth;
      const targetOffset = (34 * 90) - (screenWidth / 2) + 40;
      strip.style.transition = "transform 4s cubic-bezier(0.1, 0.7, 0.1, 1)";
      strip.style.transform = `translateX(-${targetOffset}px)`;
      
      setTimeout(async () => {
        strip.style.display = "none";
        pointer.style.display = "none";
        resultText.style.display = "block";
        resultText.innerHTML = `<h3 style="color: #55ff00;">Вам выпал скин!</h3><img src="${data.skin.image_url}" style="width: 120px; margin: 10px 0;"><div style="font-weight: bold;">${data.skin.name}</div>`;
        toast("Скин добавлен в инвентарь!");
        await loadMe(); await loadInventory();
      }, 4100);
    }, 100);
  } catch(e) { 
    toast(e.message); strip.style.display = "none"; pointer.style.display = "none";
  }
}

// --- ИНВЕНТАРЬ, АПГРЕЙД, ПРОДАЖА ---
let currentUpgradeInvId = null, currentUpgradeChance = 20;
async function loadInventory() {
  const items = await api("/api/inventory");
  $("inventory-list").innerHTML = items.map(i => `
    <div style="display: flex; flex-direction: column; gap: 10px; background: #1a1a1a; padding: 10px; border-radius: 8px; border: 1px solid #333;">
      <div style="display: flex; align-items: center; gap: 10px;">
        <img src="${i.image_url}" style="width: 50px; height: 50px;">
        <div style="flex: 1; font-size: 12px;"><div style="font-weight: bold;">${i.name}</div><div style="color: #55ff00;">${i.price} PTS</div></div>
      </div>
      <div style="display: flex; gap: 5px;">
        <button onclick="selectUpgradeSkin(${i.inv_id}, '${i.image_url}')" style="flex: 1; background: #ffaa00; border: none; padding: 6px; border-radius: 4px; font-weight: bold; font-size: 12px;">Апгрейд</button>
        <button onclick="sellSkin(${i.inv_id}, '${i.name}')" style="flex: 1; background: #f87171; border: none; padding: 6px; border-radius: 4px; font-weight: bold; color: #fff; font-size: 12px;">Продать</button>
      </div>
    </div>
  `).join("") || `<div class="empty">Нет скинов</div>`;
}
window.sellSkin = async function(invId, name) {
  if (!confirm(`Продать ${name}?`)) return;
  try { const d = await api("/api/inventory/sell", { method: "POST", body: JSON.stringify({ inv_id: invId }) }); toast(d.message); await loadMe(); await loadInventory(); } catch(e) { toast(e.message); }
}
window.selectUpgradeSkin = function(invId, imgUrl) {
  currentUpgradeInvId = invId;
  $("upgrade-interface").style.display = "block";
  $("upgrade-img-left").src = imgUrl;
  $("upgrade-result-text").innerHTML = "";
  $("upgrade-img-right").src = "https://i.imgur.com/rQAXrZd.png"; 
}
window.setChance = function(chance) {
  currentUpgradeChance = chance;
  document.querySelectorAll(".chance-btn").forEach(b => { b.style.background = "#333"; b.style.color = "#fff"; });
  event.target.style.background = "#55ff00"; event.target.style.color = "#000";
}
$("btn-do-upgrade").addEventListener("click", async () => {
  if (!currentUpgradeInvId) return toast("Выберите скин");
  $("upgrade-result-text").innerHTML = "Крутим...";
  try {
    const data = await api("/api/upgrade", { method: "POST", body: JSON.stringify({ inv_id: currentUpgradeInvId, chance: currentUpgradeChance }) });
    if (data.success) {
      $("upgrade-result-text").innerHTML = `<h3 style="color: #55ff00;">УСПЕХ! Вы получили ${data.target_skin.name}</h3>`;
    } else {
      $("upgrade-result-text").innerHTML = `<h3 style="color: #f87171;">НЕУДАЧА. Скин сгорел.</h3>`;
    }
    $("upgrade-interface").style.display = "none";
    await loadInventory();
  } catch(e) { toast(e.message); $("upgrade-result-text").innerHTML = ""; }
});

// --- АДМИНКА ---
async function loadAdminUsers() {
  if (!me?.is_admin) return;
  const userSelect = $("drop-user-id"), settingsUserSelect = $("settings-user-id");
  try {
    const users = await api("/api/admin/users");
    const options = `<option value="" disabled selected>Выберите игрока...</option>` + users.map(u => `<option value="${u.user_id}">${u.first_name || "Без имени"} (@${u.username || u.user_id})</option>`).join("");
    if (userSelect) userSelect.innerHTML = options;
    if (settingsUserSelect) settingsUserSelect.innerHTML = options;
  } catch(e) {}
  
  const skinSelect = $("force-skin-select");
  if (skinSelect) {
    try {
      const skins = await api("/api/admin/all-skins");
      skinSelect.innerHTML = `<option value="">Случайный скин</option>` + skins.map(s => `<option value="${s.id}">${s.name} — ${s.price} PTS</option>`).join("");
    } catch(e) {}
  }
}

$("admin-points-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const res = await api("/api/admin/give-points", { method: "POST", body: JSON.stringify({ target: $("admin-target").value, amount: Number($("admin-points-amount").value), action: $("admin-points-action").value }) });
    toast(res.message); $("admin-points-form").reset();
  } catch(e) { toast(e.message); }
});

$("admin-dropdown-points")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const res = await api("/api/admin/give-points", { method: "POST", body: JSON.stringify({ target: $("drop-user-id").value, amount: Number($("drop-points-amount").value), action: $("drop-points-action").value }) });
    toast(res.message); await loadAdminUsers();
  } catch(e) { toast(e.message); }
});

$("user-settings-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const data = await api("/api/admin/user-settings", { method: "POST", body: JSON.stringify({ user_id: Number($("settings-user-id").value), forced_skin_id: $("force-skin-select").value, custom_chance: Number($("custom-chance").value) }) });
    toast(data.message);
  } catch(err) { toast(err.message); }
});

$("admin-event-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/api/admin/create-event", { method: "POST", body: JSON.stringify({ sport: $("admin-sport").value, title: $("admin-title").value, o1: Number($("admin-o1").value), ox: $("admin-ox").value ? Number($("admin-ox").value) : null, o2: Number($("admin-o2").value) }) });
    toast("Матч добавлен! Уведомления уйдут через 40 секунд."); $("admin-event-form").reset(); await loadEvents(); loadSettle();
  } catch(e) { toast(e.message); }
});

$("admin-promo-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/api/admin/create-promo", { method: "POST", body: JSON.stringify({ code: $("admin-promo-code").value, reward: Number($("admin-promo-reward").value) }) });
    toast("Промокод создан!"); $("admin-promo-form").reset();
  } catch(e) { toast(e.message); }
});

$("btn-promo")?.addEventListener("click", async () => {
  const code = prompt("Введите промокод:");
  if (!code) return;
  try {
    const res = await api("/api/promo/activate", { method: "POST", body: JSON.stringify({ code }) });
    toast(res.message); await loadMe();
  } catch(e) { toast(e.message); }
});

async function loadSettle() {
  const open = events.filter(e=>e.status==="open");
  $("settle-list").innerHTML = open.map(e=>`
    <div class="settle" style="margin-bottom: 10px; background: #1a1a1a; padding: 10px; border-radius: 6px; display: flex; flex-direction: column; gap: 5px;">
      <div style="font-size: 14px;">${e.title}</div>
      <div style="font-size: 12px; color: #aaa;">Текущие Кф: ${e.odds_1} / ${e.odds_x || '-'} / ${e.odds_2}</div>
      <div style="display: flex; gap: 5px;">
        <button onclick="updateOdds(${e.id}, ${e.odds_1}, ${e.odds_x || 'null'}, ${e.odds_2})" style="background: #ffaa00; color: #000; border: none; padding: 4px 8px; border-radius: 4px;">✏️ Изменить Кф</button>
      </div>
      <div style="display: flex; gap: 5px; margin-top: 5px;">
        <button onclick="settle(${e.id}, '1')">П1</button>
        <button onclick="settle(${e.id}, 'X')">X</button>
        <button onclick="settle(${e.id}, '2')">П2</button>
      </div>
    </div>
  `).join("") || "Нет матчей";
}

window.updateOdds = async function(id, o1, ox, o2) {
  const n1 = prompt("Новый коэффициент на П1:", o1);
  if (n1 === null) return;
  const nx = prompt("Новый коэффициент на X (оставьте пустым, если нет):", ox || "");
  if (nx === null) return;
  const n2 = prompt("Новый коэффициент на П2:", o2);
  if (n2 === null) return;
  
  try {
    await api(`/api/admin/update-odds/${id}`, {
      method: "POST",
      body: JSON.stringify({ o1: Number(n1), ox: nx ? Number(nx) : null, o2: Number(n2) })
    });
    toast("Кэфы обновлены!"); await loadEvents(); loadSettle();
  } catch(e) { toast(e.message); }
}

window.settle = async function(id, result) {
  if (!confirm("Рассчитать матч?")) return;
  try { await api(`/api/admin/settle/${id}`, { method: "POST", body: JSON.stringify({ result }) }); toast("Рассчитано!"); await loadEvents(); loadSettle(); } catch(e) { toast(e.message); }
}

document.querySelectorAll(".tab").forEach(btn=>btn.addEventListener("click", async ()=>{
  document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("active",b===btn));
  document.querySelectorAll(".panel").forEach(p=>p.classList.toggle("hidden",p.id!==btn.dataset.tab));
  if(btn.dataset.tab==="history") await loadHistory();
  if(btn.dataset.tab==="rating") await loadRating();
  if(btn.dataset.tab==="news") await loadNews();
  if(btn.dataset.tab==="admin") { await loadAdminUsers(); await loadEvents(); loadSettle(); }
  if(btn.dataset.tab==="cases") { await loadCases(); await loadInventory(); }
}));

(async()=>{
  try { await loadMe(); await loadEvents(); } 
  catch(e) { $("event-list").innerHTML = `<div class="empty">${e.message}</div>`; }
})();
