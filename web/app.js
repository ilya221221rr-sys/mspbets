const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const initData = tg?.initData || "";
const headers = {"Content-Type":"application/json","X-Telegram-Init-Data":initData};
let me = null, events = [];
let prevOdds = {}; 
const $ = id => document.getElementById(id);

async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{...headers,...(options.headers||{})}});
  const data = await response.json().catch(()=>({detail:"Ошибка сервера"}));
  if (!response.ok) throw new Error(data.detail || "Ошибка запроса");
  return data;
}
function toast(message) {
  const t = $("toast"); t.textContent = message;
  t.classList.remove("hidden"); setTimeout(()=> t.classList.add("hidden"), 2600);
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
async function loadMe() {
  me = await api("/api/me");
  $("balance").textContent = me.balance.toLocaleString("ru-RU");
  if (me.is_admin) document.querySelectorAll(".admin-only").forEach(x=>x.classList.remove("hidden"));
}

async function loadEvents() {
  events = await api("/api/events");
  const open = events.filter(e=>e.status==="open");
  
  $("event-list").innerHTML = open.length ? open.map(e=>{
    const hasDraw = e.sport === "Футбол";
    const p = prevOdds[e.id] || { o1: e.odds_1, ox: e.odds_x, o2: e.odds_2 };
    let color1 = "", colorX = "", color2 = "";
    if (e.odds_1 > p.o1) color1 = "color: #4ade80;"; 
    else if (e.odds_1 < p.o1) color1 = "color: #f87171;";
    if (e.odds_2 > p.o2) color2 = "color: #4ade80;";
    else if (e.odds_2 < p.o2) color2 = "color: #f87171;";
    if (hasDraw) {
      if (e.odds_x > p.ox) colorX = "color: #4ade80;";
      else if (e.odds_x < p.ox) colorX = "color: #f87171;";
    }
    prevOdds[e.id] = { o1: e.odds_1, ox: e.odds_x, o2: e.odds_2 };

    return `
    <article class="event" data-id="${e.id}">
      <div class="event-top">
        <span>${escapeHtml(e.sport)}</span>
        <span>Кф: 
          <b style="${color1} transition: color 0.3s;">${e.odds_1}</b> 
          ${hasDraw ? `/ <b style="${colorX} transition: color 0.3s;">${e.odds_x}</b> ` : ''}
          / <b style="${color2} transition: color 0.3s;">${e.odds_2}</b>
        </span>
      </div>
      <h3>${escapeHtml(e.title)}</h3><small>${escapeHtml(e.starts_at || "Время не указано")}</small>
      ${e.my_choice ? `<p class="disclaimer">Ваш прогноз: ${e.my_choice==="1"?"П1":e.my_choice==="X"?"Ничья":"П2"} · ${e.my_points} баллов</p>` : `
      <div class="choices">
        <button class="choice" data-choice="1">П1</button>
        ${hasDraw ? `<button class="choice" data-choice="X">Ничья</button>` : ''}
        <button class="choice" data-choice="2">П2</button>
      </div>
      <div class="bet-row">
        <input class="points" type="number" min="1" max="${me.balance}" value="${Math.min(me.balance, 100)}" aria-label="Баллы">
        <button class="primary submit-prediction">Прогноз</button>
      </div>`}
    </article>`;
  }).join("") : `<div class="empty">Пока нет активных матчей.</div>`;
  
  document.querySelectorAll(".event").forEach(card=>{
    let selected = "";
    card.querySelectorAll(".choice").forEach(btn=>btn.addEventListener("click",()=>{
      selected=btn.dataset.choice;
      card.querySelectorAll(".choice").forEach(b=>b.classList.toggle("selected",b===btn));
    }));
    card.querySelector(".submit-prediction")?.addEventListener("click",async()=>{
      if(!selected) return toast("Сначала выберите исход");
      const points = Number(card.querySelector(".points").value);
      try {
        await api("/api/predict",{method:"POST",body:JSON.stringify({event_id:Number(card.dataset.id),choice:selected,points})});
        toast("Прогноз принят!");
        await refresh();
      } catch(e){toast(e.message)}
    });
  });
  if(me?.is_admin) renderSettle();
}

async function loadRating() {
  const rows = await api("/api/leaderboard");
  $("rating-list").innerHTML = rows.length ? rows.map((r,i)=>`
    <div class="rank"><span>${i+1}. ${escapeHtml(r.first_name || r.username || "Игрок")}</span><b>${Number(r.balance).toLocaleString("ru-RU")} PTS</b></div>`).join("") : `<div class="empty">Рейтинг пока пуст.</div>`;
}

async function loadAdminUsers() {
  if (!me?.is_admin) return;
  
  const userSelect = $("give-user-id");
  const skinUserSelect = $("skin-user-id");
  
  if (userSelect || skinUserSelect) {
    try {
      const users = await api("/api/admin/users");
      const userOptions = `<option value="" disabled selected>Выберите игрока...</option>` + 
        users.map(u => {
          const name = u.first_name || "Без имени";
          const uname = u.username ? `(@${u.username})` : `(ID: ${u.user_id})`;
          return `<option value="${u.user_id}">${escapeHtml(name)} ${escapeHtml(uname)}</option>`;
        }).join("");
        
      if (userSelect) userSelect.innerHTML = userOptions;
      if (skinUserSelect) skinUserSelect.innerHTML = userOptions;
    } catch(e) { console.error(e); }
  }

  const skinSelect = $("skin-select");
  if (skinSelect) {
    try {
      const skins = await api("/api/admin/all-skins");
      skinSelect.innerHTML = `<option value="" disabled selected>Выберите скин...</option>` + 
        skins.map(s => `<option value="${s.id}">${escapeHtml(s.name)} — ${s.price} PTS</option>`).join("");
    } catch(e) { console.error(e); }
  }
}

function renderSettle() {
  const open = events.filter(e=>e.status==="open");
  $("settle-list").innerHTML = open.length ? open.map(e=>{
    const hasDraw = e.sport === "Футбол";
    return `
    <div class="settle" style="flex-direction: column; align-items: flex-start; gap: 8px;">
      <div style="display: flex; justify-content: space-between; width: 100%; align-items: center;">
        <span><b>${escapeHtml(e.title)}</b> <small>(${e.sport})</small></span>
        <button onclick="updateOdds(${e.id}, ${e.odds_1}, ${e.odds_x || 'null'}, ${e.odds_2}, ${hasDraw})" style="background: none; border: 1px solid #55ff00; color: #55ff00; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px;">✏️ Кэфы</button>
      </div>
      <div style="display: flex; justify-content: space-between; width: 100%; margin-top: 4px;">
        <span style="font-size: 12px; opacity: 0.7;">Текущие: ${e.odds_1} ${hasDraw ? '/ ' + e.odds_x + ' ' : ''}/ ${e.odds_2}</span>
        <span>
          <button data-id="${e.id}" data-result="1">П1</button> 
          ${hasDraw ? `<button data-id="${e.id}" data-result="X">X</button>` : ''} 
          <button data-id="${e.id}" data-result="2">П2</button>
        </span>
      </div>
    </div>`;
  }).join("") : `<div class="empty">Нет матчей для расчёта.</div>`;
  
  $("settle-list").querySelectorAll("button[data-result]").forEach(btn=>btn.addEventListener("click",async()=>{
    if(!confirm("Подтвердить результат матча?")) return;
    try {
      await api(`/api/admin/events/${btn.dataset.id}/settle`,{method:"POST",body:JSON.stringify({result:btn.dataset.result})});
      toast("Матч рассчитан");
      await refresh();
    } catch(e){toast(e.message)}
  }));
}

window.updateOdds = async function(id, o1, ox, o2, hasDraw) {
  const newO1 = prompt("Новый коэффициент на П1:", o1);
  if (newO1 === null) return;
  let newOx = null;
  if (hasDraw) {
    newOx = prompt("Новый коэффициент на X:", ox);
    if (newOx === null) return;
  }
  const newO2 = prompt("Новый коэффициент на П2:", o2);
  if (newO2 === null) return;
  try {
    await api(`/api/admin/events/${id}/update-odds`, {
      method: "POST",
      body: JSON.stringify({
        odds_1: Number(newO1),
        odds_x: hasDraw ? Number(newOx) : null,
        odds_2: Number(newO2)
      })
    });
    toast("Коэффициенты обновлены");
    await refresh();
  } catch(e) { toast(e.message); }
}

$("create-event").addEventListener("submit",async e=>{
  e.preventDefault();
  try {
    await api("/api/admin/events",{method:"POST",body:JSON.stringify({
      sport: $("sport").value,
      title: $("title").value,
      starts_at: $("starts").value,
      odds_1: Number($("odds_1").value),
      odds_x: $("sport").value === "Футбол" ? Number($("odds_x").value) : null,
      odds_2: Number($("odds_2").value)
    })});
    $("create-event").reset(); 
    toast("Матч добавлен"); 
    await refresh();
  } catch(err){toast(err.message)}
});

$("give-points")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const data = await api("/api/admin/give-points", {
      method: "POST",
      body: JSON.stringify({
        user_id: Number($("give-user-id").value),
        amount: Number($("give-amount").value),
        action: $("points-action").value
      })
    });
    $("give-points").reset();
    toast(data.message || "Успешно");
    await loadAdminUsers();
  } catch (err) { toast(err.message); }
});

$("create-promo")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const data = await api("/api/admin/create-promo", {
      method: "POST",
      body: JSON.stringify({
        code: $("promo-code").value,
        reward: Number($("promo-reward").value)
      })
    });
    $("create-promo").reset();
    toast(data.message || "Промокод создан");
  } catch (err) { toast(err.message); }
});

$("give-skin-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const data = await api("/api/admin/give-skin", {
      method: "POST",
      body: JSON.stringify({
        user_id: Number($("skin-user-id").value),
        skin_id: Number($("skin-select").value),
        force_next: $("force-next-case").checked
      })
    });
    $("give-skin-form").reset();
    toast(data.message || "Скин выдан!");
  } catch (err) {
    toast(err.message);
  }
});

$("btn-promo").addEventListener("click", async () => {
  const code = prompt("Введите ваш промокод:");
  if (!code) return;
  try {
    const data = await api("/api/promo", { method: "POST", body: JSON.stringify({ code: code }) });
    toast(data.message || "Промокод активирован!");
    await loadMe();
  } catch(err) { toast(err.message); }
});

async function loadCases() {
  const cases = await api("/api/cases");
  $("cases-list").innerHTML = cases.map(c => `
    <div style="background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 15px; text-align: center;">
      <img src="${c.image_url}" style="width: 100%; border-radius: 4px; margin-bottom: 10px;">
      <div style="font-weight: bold; margin-bottom: 5px;">${escapeHtml(c.name)}</div>
      <div style="color: #55ff00; margin-bottom: 10px;">${c.price} PTS</div>
      <button onclick="openCase(${c.id})" class="primary" style="width: 100%;">Открыть</button>
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
  
  try {
    const data = await api("/api/cases/open", { method: "POST", body: JSON.stringify({ case_id: caseId }) });
    
    const fakeSkins = [
      "https://i.imgur.com/G7XpkYs.png", "https://i.imgur.com/0QWoP2i.png", 
      "https://i.imgur.com/7x8YDiz.png", "https://i.imgur.com/BegS1KI.png",
      "https://i.imgur.com/AQiM6I8.png"
    ];
    
    let stripHTML = "";
    for (let i = 0; i < 30; i++) {
      const randomImg = fakeSkins[Math.floor(Math.random() * fakeSkins.length)];
      stripHTML += `<img src="${randomImg}" style="width: 80px; height: 80px; border-radius: 4px; border: 1px solid #333;">`;
    }
    
    stripHTML += `<img src="${data.skin.image_url}" style="width: 80px; height: 80px; border-radius: 4px; border: 2px solid #55ff00; box-shadow: 0 0 10px #55ff00;">`;
    
    for (let i = 0; i < 5; i++) {
      const randomImg = fakeSkins[Math.floor(Math.random() * fakeSkins.length)];
      stripHTML += `<img src="${randomImg}" style="width: 80px; height: 80px; border-radius: 4px; border: 1px solid #333;">`;
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
        resultText.innerHTML = `
          <h3 style="color: #55ff00;">Вам выпал скин!</h3>
          <img src="${data.skin.image_url}" style="width: 120px; border-radius: 8px; margin: 10px 0;">
          <div style="font-size: 18px; font-weight: bold;">${escapeHtml(data.skin.name)}</div>
          <div style="color: #55ff00;">${data.skin.price} PTS</div>
        `;
        toast("Скин добавлен в инвентарь!");
        await loadMe();
        await loadInventory();
      }, 4100);
    }, 100);
  } catch(e) { 
    toast(e.message);
    strip.style.display = "none";
    pointer.style.display = "none";
  }
}

let currentUpgradeInvId = null;
let currentUpgradeChance = 20; 

async function loadInventory() {
  const items = await api("/api/inventory");
  $("inventory-list").innerHTML = items.length ? items.map(i => `
    <div style="display: flex; flex-direction: column; gap: 10px; background: #1a1a1a; padding: 10px; border-radius: 8px; border: 1px solid #333;">
      <div style="display: flex; align-items: center; gap: 10px;">
        <img src="${i.image_url}" style="width: 50px; height: 50px; border-radius: 4px;">
        <div style="flex: 1; font-size: 12px;">
          <div style="font-weight: bold;">${escapeHtml(i.name)}</div>
          <div style="color: #55ff00;">${i.price} PTS</div>
        </div>
      </div>
      <div style="display: flex; gap: 5px;">
        <button onclick="selectUpgradeSkin(${i.inv_id}, '${i.image_url}')" style="flex: 1; background: #ffaa00; color: black; border: none; padding: 6px 10px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 12px;">Апгрейд</button>
        <button onclick="sellSkin(${i.inv_id}, '${escapeHtml(i.name)}')" style="flex: 1; background: #f87171; color: white; border: none; padding: 6px 10px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 12px;">Продать</button>
      </div>
    </div>
  `).join("") : `<div class="empty">У вас пока нет скинов</div>`;
}

window.sellSkin = async function(invId, skinName) {
  if (!confirm(`Вы уверены, что хотите продать ${skinName}?`)) return;
  try {
    const data = await api("/api/inventory/sell", { method: "POST", body: JSON.stringify({ inv_id: invId }) });
    toast(data.message || "Скин успешно продан!");
    await loadMe();
    await loadInventory();
  } catch(e) { toast(e.message); }
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
  document.querySelectorAll(".chance-btn").forEach(b => {
    b.style.background = "#333";
    b.style.color = "#fff";
  });
  event.target.style.background = "#55ff00";
  event.target.style.color = "#000";
}

$("btn-do-upgrade").addEventListener("click", async () => {
  if (!currentUpgradeInvId) return toast("Выберите скин");
  $("upgrade-result-text").innerHTML = "Крутим...";
  
  try {
    const data = await api("/api/upgrade", { 
      method: "POST", 
      body: JSON.stringify({ inv_id: currentUpgradeInvId, chance: currentUpgradeChance }) 
    });
    
    if (data.success) {
      $("upgrade-result-text").innerHTML = `<h3 style="color: #55ff00;">УСПЕХ! Вы получили ${escapeHtml(data.target_skin.name)}</h3>`;
      toast("Апгрейд успешен!");
    } else {
      $("upgrade-result-text").innerHTML = `<h3 style="color: #f87171;">НЕУДАЧА. Скин сгорел.</h3>`;
      toast("Скин сгорел");
    }
    
    $("upgrade-interface").style.display = "none";
    await loadInventory();
  } catch(e) {
    toast(e.message);
    $("upgrade-result-text").innerHTML = "";
  }
});

document.querySelectorAll(".tab").forEach(btn=>btn.addEventListener("click",async()=>{
  document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("active",b===btn));
  document.querySelectorAll(".panel").forEach(p=>p.classList.toggle("hidden",p.id!==btn.dataset.tab));
  
  if(btn.dataset.tab==="rating") await loadRating();
  if(btn.dataset.tab==="admin") await loadAdminUsers();
  if(btn.dataset.tab==="cases") { await loadCases(); await loadInventory(); }
}));

async function refresh(){await loadMe();await loadEvents()}

(async()=>{
  try { await refresh(); }
  catch(e){$("event-list").innerHTML=`<div class="empty">${escapeHtml(e.message)}<br><br>Откройте Mini App через Telegram-бота.</div>`;}
})();

setInterval(async () => {
  if (document.getElementById("matches").classList.contains("hidden") === false) {
    try {
      const newEvents = await api("/api/events");
      if (JSON.stringify(newEvents) !== JSON.stringify(events)) {
        events = newEvents;
        loadEvents();
      }
    } catch(e) {}
  }
}, 15000);
