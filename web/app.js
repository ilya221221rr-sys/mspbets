const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const initData = tg?.initData || "";
const headers = {"Content-Type":"application/json","X-Telegram-Init-Data":initData};
let me = null, events = [];
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

// ... (loadEvents, loadRating, renderSettle, createEvent, givePoints, createPromo, activatePromo - ОСТАВЬ КАК БЫЛО) ...

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

// --- АНИМАЦИЯ КЕЙСА ---
window.openCase = async function(caseId) {
  const resultContainer = $("case-result");
  const strip = $("case-strip");
  const pointer = $("case-pointer");
  const resultText = $("case-result-text");
  
  resultText.style.display = "none";
  strip.style.display = "flex";
  pointer.style.display = "block";
  
  try {
    const data = await api("/api/cases/open", { method: "POST", body: JSON.stringify({ case_id: caseId }) });
    
    // Генерируем ленту
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
    
    // Добавляем выигранный скин на 35-е место (индекс 34)
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

// --- ИНВЕНТАРЬ ---
async function loadInventory() {
  const items = await api("/api/inventory");
  $("inventory-list").innerHTML = items.length ? items.map(i => `
    <div style="display: flex; align-items: center; gap: 10px; background: #1a1a1a; padding: 10px; border-radius: 8px; border: 1px solid #333;">
      <img src="${i.image_url}" style="width: 50px; height: 50px; border-radius: 4px;">
      <div style="flex: 1; font-size: 12px;">
        <div style="font-weight: bold;">${escapeHtml(i.name)}</div>
        <div style="color: #55ff00;">${i.price} PTS</div>
      </div>
      <button onclick="upgradeSkin(${i.inv_id})" style="background: #ffaa00; color: black; border: none; padding: 6px 10px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 12px;">Апгрейд</button>
    </div>
  `).join("") : `<div class="empty">У вас пока нет скинов</div>`;
}

// --- АНИМАЦИЯ АПГРЕЙДА ---
window.upgradeSkin = async function(invId) {
  if (!confirm("Шанс апгрейда 30%. При неудаче скин сгорает. Вы уверены?")) return;
  
  const resultContainer = $("upgrade-result");
  const strip = $("upgrade-strip");
  const resultText = $("upgrade-text");
  
  resultContainer.style.display = "block";
  strip.style.display = "flex";
  resultText.style.display = "none";
  
  try {
    const data = await api("/api/upgrade", { method: "POST", body: JSON.stringify({ inv_id: invId }) });
    
    // В апгрейдере лента состоит из старого скина и целевого
    const imgOld = data.old_skin.image_url;
    const imgNew = data.target_skin.image_url;
    
    let stripHTML = "";
    // 30 картинок старого скина
    for (let i = 0; i < 30; i++) {
      stripHTML += `<img src="${imgOld}" style="width: 80px; height: 80px; border-radius: 4px; border: 1px solid #333;">`;
    }
    
    // Вставляем целевой скин на 35-е место
    stripHTML += `<img src="${imgNew}" style="width: 80px; height: 80px; border-radius: 4px; border: 2px solid #ffaa00; box-shadow: 0 0 10px #ffaa00;">`;
    
    // Добавляем еще старых скинов для фона
    for (let i = 0; i < 5; i++) {
      stripHTML += `<img src="${imgOld}" style="width: 80px; height: 80px; border-radius: 4px; border: 1px solid #333;">`;
    }
    
    strip.innerHTML = stripHTML;
    strip.style.transition = "none";
    strip.style.transform = `translateX(0)`;
    
    setTimeout(() => {
      const screenWidth = window.innerWidth;
      // Останавливаем указатель ровно на 35-м элементе
      const targetOffset = (34 * 90) - (screenWidth / 2) + 40;
      
      strip.style.transition = "transform 4s cubic-bezier(0.1, 0.7, 0.1, 1)";
      strip.style.transform = `translateX(-${targetOffset}px)`;
      
      setTimeout(async () => {
        strip.style.display = "none";
        resultText.style.display = "block";
        
        if (data.success) {
          resultText.innerHTML = `
            <h3 style="color: #55ff00;">УСПЕХ!</h3>
            <div style="display: flex; align-items: center; justify-content: center; gap: 10px; margin: 10px 0;">
              <img src="${data.old_skin.image_url}" style="width: 50px; opacity: 0.5;">
              <span>➜</span>
              <img src="${data.target_skin.image_url}" style="width: 80px; border: 2px solid #55ff00; border-radius: 8px;">
            </div>
            <div style="font-weight: bold;">${escapeHtml(data.target_skin.name)}</div>
            <div style="color: #55ff00;">${data.target_skin.price} PTS</div>
          `;
          toast("Апгрейд успешен!");
        } else {
          resultText.innerHTML = `
            <h3 style="color: #f87171;">НЕУДАЧА</h3>
            <img src="${data.old_skin.image_url}" style="width: 60px; opacity: 0.3; margin: 10px 0;">
            <div style="color: #f87171;">Скин сгорел</div>
          `;
          toast("Скин сгорел");
        }
        
        await loadInventory();
      }, 4100);
    }, 100);
    
  } catch(e) {
    toast(e.message);
    strip.style.display = "none";
    resultContainer.style.display = "none";
  }
}

// Обнови обработчик вкладок
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
