/* ==========================================================
   Праздничная тема: выход GTA VI (19 ноября 2026) — приложение в цветах Vice City.
   Включается сама на одну неделю и сама выключается. Подключается в <head>, чтобы не было «мигания».

   Как поменять период — правьте две даты ниже (локальное время устройства игрока).
   Как выключить совсем — поставьте GTA6_FROM и GTA6_TO в прошлое (или удалите строку со скриптом в index.html).
   Для проверки в любой день: откройте приложение с ?theme=gta6 (включить) или ?theme=default (выключить).
   ========================================================== */
(function () {
  var GTA6_FROM = new Date(2026, 10, 20, 0, 0, 0);  // 20 ноября 2026 (месяцы с 0: 10 = ноябрь)
  var GTA6_TO = new Date(2026, 10, 27, 0, 0, 0);    // до 27 ноября 2026 00:00 — ровно неделя

  var on = false;
  try {
    var q = new URLSearchParams(location.search).get('theme');
    if (q === 'gta6') { on = true; try { sessionStorage.setItem('msp_theme', 'gta6'); } catch (e) {} }
    else if (q === 'default') { on = false; try { sessionStorage.setItem('msp_theme', 'default'); } catch (e) {} }
    else {
      var saved = null; try { saved = sessionStorage.getItem('msp_theme'); } catch (e) {}
      if (saved) on = saved === 'gta6';
      else { var now = new Date(); on = now >= GTA6_FROM && now < GTA6_TO; }
    }
  } catch (e) {}

  var BG = on ? '#12061F' : '#090A12';
  window.MSP_THEME = { name: on ? 'gta6' : 'default', bg: BG };
  if (on) {
    document.documentElement.setAttribute('data-theme', 'gta6');
    document.addEventListener('DOMContentLoaded', function () {
      var m = document.querySelector('meta[name="theme-color"]');
      if (m) m.setAttribute('content', BG);
    });
  }
})();
