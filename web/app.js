const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
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
  const t = $("toast");
  t.textContent = message;
  t.classList.remove("hidden");
  setTimeout(()=> t.classList.add("hidden"), 2600);
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
    return `
    <article class="event" data-id="${e.id}">
      <div class="event-top"><span>${escapeHtml(e.sport)}</span><span>Кф: ${e.odds_1} / ${hasDraw ? e.odds_x + ' / ' : ''}${e.odds_2}</span></div>
      <h3>${escapeHtml(e.title)}</h3><small>${escapeHtml(e.starts_at || "Время не указано")}</small>
      ${e.my_choice ? `<p class="disclaimer">Ваш прогноз: ${e.my_choice==="1"?"П1":e.my_choice==="X"?"Ничья":"П2"} · ${e.my_points} баллов</p>` : `
      <div class="choices">
        <button class="choice" data-choice="1">П1</button>
        ${hasDraw ? `<button class="choice" data-choice="X">Ничья</button>` : ''}
        <button class="choice" data-choice="2">П2</button>
      </div>
      <div class="bet-row"><input class="points" type="number" min="1" max="1000" value="100" aria-label="Баллы"><button class="primary submit-prediction">Прогноз</button></div>`}
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

function renderSettle() {
  const open = events.filter(e=>e.status==="open");
  $("settle-list").innerHTML = open.length ? open.map(e=>{
    const hasDraw = e.sport === "Футбол";
    return `
    <div class="settle"><span>${escapeHtml(e.title)}</span>
      <span>
        <button data-id="${e.id}" data-result="1">П1</button> 
        ${hasDraw ? `<button data-id="${e.id}" data-result="X">X</button>` : ''} 
        <button data-id="${e.id}" data-result="2">П2</button>
      </span>
    </div>`;
  }).join("") : `<div class="empty">Нет матчей для расчёта.</div>`;
  
  $("settle-list").querySelectorAll("button").forEach(btn=>btn.addEventListener("click",async()=>{
    if(!confirm("Подтвердить результат матча?")) return;
    try {
      await api(`/api/admin/events/${btn.dataset.id}/settle`,{method:"POST",body:JSON.stringify({result:btn.dataset.result})});
      toast("Матч рассчитан");
      await refresh();
    } catch(e){toast(e.message)}
  }));
}

// Обработчик добавления матча (с новыми полями)
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

// Обработчик начисления баллов
$("give-points")?.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const data = await api("/api/admin/give-points", {
      method: "POST",
      body: JSON.stringify({
        user_id: Number($("give-user-id").value),
        amount: Number($("give-amount").value)
      })
    });
    $("give-points").reset();
    toast(data.message || "Баллы начислены");
  } catch (err) {
    toast(err.message);
  }
});

document.querySelectorAll(".tab").forEach(btn=>btn.addEventListener("click",async()=>{
  document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("active",b===btn));
  document.querySelectorAll(".panel").forEach(p=>p.classList.toggle("hidden",p.id!==btn.dataset.tab));
  if(btn.dataset.tab==="rating") await loadRating();
}));

async function refresh(){await loadMe();await loadEvents()}

(async()=>{
  try { await refresh(); }
  catch(e){$("event-list").innerHTML=`<div class="empty">${escapeHtml(e.message)}<br><br>Откройте Mini App через Telegram-бота.</div>`;}
})();
