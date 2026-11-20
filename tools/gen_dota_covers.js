// Генерирует обложки Dota-кейсов (web/cases/dota_*.webp): веер из 3 реальных предметов кейса на цветном фоне.
// Запуск (нужен пакет sharp):  node tools/gen_dota_covers.js
const sharp = require("sharp"), path = require("path");
const ROOT = path.resolve(__dirname, "..");
const COVERS = {
  dota_gold: { hue: [48, 95, 55], items: ["golden_edge_of_the_lost_order", "genuine_rapier_of_the_burning_god", "golden_shadow_masquerade"] },
  dota_immortal: { hue: [352, 85, 55], items: ["concord_reversion", "dragonclaw_hook", "blade_of_tears"] },
  dota_arcana: { hue: [150, 80, 45], items: ["manifold_paradox", "fractal_horns_of_inner_abysm", "demon_eater"] },
};
const S = 256, R = 22;
const mask = (w, h, r) => Buffer.from(`<svg width="${w}" height="${h}"><rect width="${w}" height="${h}" rx="${r}" fill="#fff"/></svg>`);
(async () => {
  for (const [name, c] of Object.entries(COVERS)) {
    const [h, sat, l] = c.hue;
    const bg = Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}"><defs>
      <linearGradient id="b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${h},45%,17%)"/><stop offset="1" stop-color="hsl(${h},55%,6%)"/></linearGradient>
      <radialGradient id="g" cx=".5" cy=".55" r=".6"><stop offset="0" stop-color="hsl(${h},${sat}%,${l}%)" stop-opacity=".75"/><stop offset="1" stop-color="hsl(${h},${sat}%,${l}%)" stop-opacity="0"/></radialGradient></defs>
      <rect width="${S}" height="${S}" rx="${R}" fill="url(#b)"/><rect width="${S}" height="${S}" rx="${R}" fill="url(#g)"/>
      <rect x="1" y="1" width="${S - 2}" height="${S - 2}" rx="${R - 1}" fill="none" stroke="#fff" stroke-opacity=".2" stroke-width="2"/></svg>`);
    const cards = [];
    const specs = [[-16, 28, 40], [16, 128, 40], [0, 74, 70]]; // [угол, left, top] для левой, правой и центральной карточки
    const order = [0, 1, 2];
    for (const i of order) {
      const file = path.join(ROOT, "web", "dota", c.items[i] + ".webp");
      const w = i === 2 ? 150 : 112, hgt = Math.round(w * 0.75);
      let card = await sharp(file).trim({ threshold: 1 }).resize(w, hgt, { fit: "cover" }).composite([{ input: mask(w, hgt, 10), blend: "dest-in" }]).png().toBuffer();
      const border = Buffer.from(`<svg width="${w}" height="${hgt}"><rect x="1" y="1" width="${w - 2}" height="${hgt - 2}" rx="9" fill="none" stroke="hsl(${h},${sat}%,${l + 20}%)" stroke-width="2"/></svg>`);
      card = await sharp(card).composite([{ input: border }]).png().toBuffer();
      const [angle, left, top] = specs[i];
      const rot = await sharp(card).rotate(angle, { background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toBuffer();
      const m = await sharp(rot).metadata();
      cards.push({ input: rot, left: Math.max(0, Math.round(left - (m.width - w) / 2)), top: Math.max(0, Math.round(top + 36 - (m.height - hgt) / 2)) });
    }
    const out = path.join(ROOT, "web", "cases", name + ".webp");
    await sharp(bg).composite(cards).webp({ quality: 90 }).toFile(out);
    console.log("ok", out);
  }
})();
