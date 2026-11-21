"""
extras/catalog.py — собственные кейсы и скины MSPBETS.

Математика:
  * цена скина = цена кейса × множитель (из таблицы ниже);
  * вероятность предмета ~ price^(-alpha); alpha подбирается бисекцией так, чтобы
    RTP кейса = заданному (90–95%). Веса целые (сумма 1 000 000) — и именно они
    используются при выпадении, поэтому шанс на экране == реальный шанс;
  * RTP считается по этим же целым весам: sum(weight_i * price_i) / sum(weight) / price_case.
"""
import math

WEIGHT_TOTAL = 1_000_000

# (имя, иконка, множитель цены)
CASES = [
    dict(
        key="yard", name="Дворовый", price=150, rtp=0.90, hue=205, icon="ball",
        desc="Для старта: недорогие предметы и шанс на золотой свисток.",
        items=[
            ("Свисток Физрука", "whistle", 0.20), ("Шнурки Забияки", "bolt", 0.40),
            ("Мяч «Дворовый»", "ball", 0.65), ("Перчатки Вратаря", "glove", 0.90),
            ("Бутса Стрит", "boot", 1.40), ("Кепка Фаната", "shield", 2.50),
            ("Медаль Двора", "medal", 6.0), ("Кубок Района", "cup", 15.0),
            ("Золотой Свисток", "whistle", 40.0),
        ],
    ),
    dict(
        key="striker", name="Бутса Форварда", price=500, rtp=0.92, hue=150, icon="boot",
        desc="Экипировка нападающих. Золотая Бутса — мечта каждого.",
        items=[
            ("Гетры Нападающего", "star", 0.20), ("Капитанская Повязка", "shield", 0.40),
            ("Мяч «Матч-Дэй»", "ball", 0.65), ("Бутса «Молния»", "boot", 0.90),
            ("Перчатки Pro", "glove", 1.40), ("Бутса «Феникс»", "flame", 2.50),
            ("Шипы Бомбардира", "bolt", 6.0), ("Золотая Бутса", "boot", 15.0),
            ("Бутса «Галактика»", "rocket", 40.0),
        ],
    ),
    dict(
        key="hattrick", name="Хет-трик", price=1500, rtp=0.92, hue=35, icon="cup",
        desc="Три гола — три шанса. Кубки, короны и хрустальный хет-трик.",
        items=[
            ("Жетон Тренера", "medal", 0.20), ("Табло Победы", "clock", 0.40),
            ("Мяч «Хет-трик»", "ball", 0.65), ("Свисток Арбитра", "whistle", 0.90),
            ("Серебряный Кубок", "cup", 1.40), ("Щит Защитника", "shield", 2.50),
            ("Корона Бомбардира", "crown", 6.0), ("Кубок Лиги", "cup", 15.0),
            ("Хрустальный Хет-трик", "gem", 40.0),
        ],
    ),
    dict(
        key="goldball", name="Золотой Мяч", price=4000, rtp=0.93, hue=48, icon="star",
        desc="Награда лучшему игроку года. Только достойные предметы.",
        items=[
            ("Билет на Финал", "star", 0.20), ("Перчатка Чемпиона", "glove", 0.40),
            ("Мяч «Финал»", "ball", 0.65), ("Часы Тайм-аута", "clock", 0.90),
            ("Кубок Континента", "cup", 1.40), ("Сердце Болельщика", "heart", 2.50),
            ("Корона Капитана", "crown", 6.0), ("Золотой Мяч", "ball", 15.0),
            ("Платиновая Звезда", "star", 40.0),
        ],
    ),
    dict(
        key="cyber", name="Кибер-арена", price=10000, rtp=0.94, hue=275, icon="controller",
        desc="Киберспортивный кейс: гарнитуры, геймпады и легендарная рапира.",
        items=[
            ("Мышь «Клик»", "target", 0.20), ("Гарнитура Pro", "headset", 0.40),
            ("Геймпад Чемпиона", "controller", 0.65), ("Часы Таймаута", "clock", 0.90),
            ("Кубик Удачи", "dice", 1.40), ("Череп Нуб-Киллера", "skull", 2.50),
            ("Сердце Команды", "heart", 6.0), ("Аркана MSP", "flame", 15.0),
            ("Рапира Immortal", "gem", 40.0),
        ],
    ),
    dict(
        key="legend", name="Легенда MSP", price=25000, rtp=0.95, hue=335, icon="crown",
        desc="Самый редкий кейс. Лучший RTP и предметы на сотни тысяч.",
        items=[
            ("Клевер Фортуны", "clover", 0.20), ("Перо Феникса", "flame", 0.40),
            ("Щит Титана", "shield", 0.65), ("Молот Грома", "bolt", 0.90),
            ("Корона Чемпиона", "crown", 1.40), ("Ракета Победы", "rocket", 2.50),
            ("Алмазное Сердце", "heart", 6.0), ("Звезда MSPBETS", "star", 15.0),
            ("Бриллиант Легенды", "gem", 40.0),
        ],
    ),
]


# --------------------------------------------------------------------------
# DOTA 2: кейсы с НАСТОЯЩИМИ предметами (картинки — оригинальные иконки Steam, лежат в web/dota/).
# usd — цена предмета на Торговой площадке Steam (снимок на момент добавления);
# в PTS: usd * DOTA_RATE (1 $ = 1000 PTS). Шанс выпадения считается так же, как в обычных кейсах:
# веса подбираются под заданный RTP, а шанс на экране == реальный.
# Чтобы обновить цены — поменяйте usd. Картинка предмета: web/dota/<img>.webp
# --------------------------------------------------------------------------
DOTA_RATE = 1000

DOTA_CASES = [
    dict(
        key="dota_gold", name="Dota 2: Золотой тайник", price=1500, rtp=0.92, hue=45, real=True,
        image="/web/cases/dota_gold.webp",
        desc="Золотые Immortal-предметы и Genuine Rapier — недорогой вход в мир Dota 2.",
        items=[
            ("Golden Edict of Shadows", 0.37, "golden_edict_of_shadows"),
            ("Crownfall - Dire Creeps", 0.49, "crownfall_dire_creeps"),
            ("Golden Eye of Ix'yxa", 0.65, "golden_eye_of_ixyxa"),
            ("Golden Twilight Schism", 0.77, "golden_twilight_schism"),
            ("Golden Edge of the Lost Order", 1.81, "golden_edge_of_the_lost_order"),
            ("Golden Profane Union", 2.00, "golden_profane_union"),
            ("Altar Ball", 2.38, "altar_ball"),
            ("Golden Shadow Masquerade", 4.41, "golden_shadow_masquerade"),
            ("Genuine Rapier of the Burning God", 7.76, "genuine_rapier_of_the_burning_god"),
        ],
    ),
    dict(
        key="dota_immortal", name="Dota 2: Бессмертные", price=6000, rtp=0.93, hue=0, real=True,
        image="/web/cases/dota_immortal.webp",
        desc="Популярные Immortal-предметы. В самом низу шанса — легендарный Dragonclaw Hook.",
        items=[
            ("Shadow Masquerade", 1.10, "shadow_masquerade"),
            ("Bracers of the Cavern Luminar", 1.81, "bracers_of_the_cavern_luminar"),
            ("Phantom Concord", 1.90, "phantom_concord"),
            ("Primal Paean", 4.43, "primal_paean"),
            ("Chaos Arbiter", 5.47, "chaos_arbiter"),
            ("Arms of Desolation", 11.35, "arms_of_desolation"),
            ("Origin of the Unmaking", 12.65, "origin_of_the_unmaking"),
            ("Golden Basher Blades", 17.17, "golden_basher_blades"),
            ("Codicil of the Veiled Ones", 19.34, "codicil_of_the_veiled_ones"),
            ("Blade of Tears", 34.50, "blade_of_tears"),
            ("Concord Reversion", 41.00, "concord_reversion"),
            ("Dragonclaw Hook", 403.13, "dragonclaw_hook"),
        ],
    ),
    dict(
        key="dota_arcana", name="Dota 2: Арканы", price=20000, rtp=0.94, hue=140, real=True,
        image="/web/cases/dota_arcana.webp",
        desc="Самые популярные арканы Dota 2: Manifold Paradox, Demon Eater, Bladeform Legacy и другие.",
        items=[
            ("Bladeform Legacy - Mask", 0.49, "bladeform_legacy_mask"),
            ("Feast of Abscession - Back", 0.59, "feast_of_abscession_back"),
            ("Rune of the Duelist Indomitable", 0.95, "rune_of_the_duelist_indomitable"),
            ("Swine Proximity Mine", 0.97, "swine_proximity_mine"),
            ("The Resurrection of Shen - Wings", 18.31, "the_resurrection_of_shen_wings"),
            ("Frost Avalanche", 19.68, "frost_avalanche"),
            ("Demon Eater", 20.84, "demon_eater"),
            ("The Devotions of Dragonus - Wings", 21.93, "the_devotions_of_dragonus_wings"),
            ("Feast of Abscession", 22.69, "feast_of_abscession"),
            ("Tempest Helm of the Thundergod", 24.50, "tempest_helm_of_the_thundergod"),
            ("Fiery Soul of the Slayer", 24.52, "fiery_soul_of_the_slayer"),
            ("Great Sage's Reckoning", 25.15, "great_sages_reckoning"),
            ("Manifold Paradox", 26.50, "manifold_paradox"),
            ("Bladeform Legacy", 29.98, "bladeform_legacy"),
            ("Swine of the Sunken Galley", 30.89, "swine_of_the_sunken_galley"),
            ("The Magus Cypher", 31.41, "the_magus_cypher"),
            ("Flockheart's Gamble", 32.91, "flockhearts_gamble"),
            ("Fractal Horns of Inner Abysm", 35.32, "fractal_horns_of_inner_abysm"),
            ("Blades of Voth Domosh", 38.99, "blades_of_voth_domosh"),
        ],
    ),
]

# --------------------------------------------------------------------------
# CS2: кейсы с НАСТОЯЩИМИ популярными скинами (картинки — иконки Steam, лежат в web/cs2/).
# usd — примерная цена на Торговой площадке Steam (снимок на момент добавления, Field-Tested/Factory New);
# в PTS: usd * rate (для CS2 1 $ = 100 PTS, т.к. скины заметно дороже предметов Dota 2).
# Веса подбираются под заданный RTP, шанс на экране == реальному. Чтобы обновить цены — поменяйте usd.
# Картинка предмета: web/cs2/<img>.webp
# --------------------------------------------------------------------------
CS2_RATE = 100

CS2_CASES = [
    dict(
        key="cs2_popular", name="CS2: Популярные скины", price=5000, rtp=0.92, hue=210, real=True,
        rate=CS2_RATE, dir="cs2", image="/web/cases/cs2_popular.webp",
        desc="Самые популярные скины CS2: AK-47 Redline, AWP Asiimov, M4A1-S Hyper Beast, Vulcan и другие.",
        items=[
            ("AK-47 | Slate", 6.10, "ak47_slate"),
            ("AWP | Atheris", 6.51, "awp_atheris"),
            ("AK-47 | Frontside Misty", 21.68, "ak47_frontside_misty"),
            ("Glock-18 | Water Elemental", 25.50, "glock18_water_elemental"),
            ("AK-47 | Redline", 43.56, "ak47_redline"),
            ("AWP | Chromatic Aberration", 46.69, "awp_chromatic_aberration"),
            ("AWP | Hyper Beast", 47.51, "awp_hyper_beast"),
            ("M4A4 | Neo-Noir", 49.34, "m4a4_neo_noir"),
            ("Desert Eagle | Printstream", 52.48, "deagle_printstream"),
            ("AK-47 | Asiimov", 57.57, "ak47_asiimov"),
            ("AWP | Neo-Noir", 57.17, "awp_neo_noir"),
            ("AK-47 | Neon Rider", 63.98, "ak47_neon_rider"),
            ("USP-S | Orion", 72.10, "usps_orion"),
            ("M4A4 | The Emperor", 80.52, "m4a4_the_emperor"),
            ("USP-S | Kill Confirmed", 99.51, "usps_kill_confirmed"),
            ("M4A1-S | Hyper Beast", 154.47, "m4a1s_hyper_beast"),
            ("AWP | Asiimov", 161.78, "awp_asiimov"),
            ("M4A1-S | Printstream", 274.80, "m4a1s_printstream"),
            ("AK-47 | Vulcan", 277.79, "ak47_vulcan"),
        ],
    ),
    dict(
        key="cs2_legend", name="CS2: Ножи и легенды", price=40000, rtp=0.94, hue=28, real=True,
        rate=CS2_RATE, dir="cs2", image="/web/cases/cs2_legend.webp",
        desc="Топовые скины CS2: Karambit Doppler, Butterfly Fade, AK-47 Fire Serpent, Wild Lotus и перчатки.",
        items=[
            ("M4A1-S | Hyper Beast", 154.47, "m4a1s_hyper_beast"),
            ("AWP | Asiimov", 161.78, "awp_asiimov"),
            ("M4A1-S | Cyrex", 238.38, "m4a1s_cyrex"),
            ("MP9 | Hot Rod", 270.36, "mp9_hot_rod"),
            ("AK-47 | Vulcan", 277.79, "ak47_vulcan"),
            ("AK-47 | Case Hardened", 293.90, "ak47_case_hardened"),
            ("SSG 08 | Dragonfire", 307.47, "ssg08_dragonfire"),
            ("★ Sport Gloves | Vice", 634.10, "sport_gloves_vice"),
            ("AWP | Lightning Strike", 663.01, "awp_lightning_strike"),
            ("Desert Eagle | Blaze", 1030.14, "deagle_blaze"),
            ("AK-47 | Fire Serpent", 1209.11, "ak47_fire_serpent"),
            ("★ M9 Bayonet | Doppler", 1385.15, "m9_bayonet_doppler"),
            ("★ Karambit | Tiger Tooth", 1427.82, "karambit_tiger_tooth"),
            ("★ Karambit | Doppler", 1700.24, "karambit_doppler"),
            ("★ Butterfly Knife | Fade", 1810.19, "butterfly_fade"),
            ("AK-47 | Wild Lotus", 1848.11, "ak47_wild_lotus"),
            ("★ Karambit | Fade", 1874.11, "karambit_fade"),
        ],
    ),
]

ALL_CASES = CASES + DOTA_CASES + CS2_CASES


def rarity_for(price, case_price):
    m = price / case_price
    if m < 0.6:
        return "Common"
    if m < 1.6:
        return "Rare"
    if m < 6.5:
        return "Epic"
    if m < 20:
        return "Legendary"
    return "Immortal"


def rarity_abs(price):
    """Редкость настоящих предметов Dota 2 — по абсолютной цене (а не относительно цены кейса)."""
    if price < 1500:
        return "Common"
    if price < 4000:
        return "Rare"
    if price < 12000:
        return "Epic"
    if price < 35000:
        return "Legendary"
    return "Immortal"


def item_prices(case):
    out = []
    if case.get("real"):
        rate = case.get("rate", DOTA_RATE)
        for _, usd, _ in case["items"]:
            p = usd * rate
            step = 10 if p < 100000 else 100
            out.append(max(1, int(round(p / step) * step)))
        return out
    for _, _, mult in case["items"]:
        p = case["price"] * mult
        step = 1 if p < 200 else 5 if p < 2000 else 10 if p < 20000 else 50
        out.append(max(1, int(round(p / step) * step)))
    return out


def solve_weights(prices, case_price, target_rtp):
    """Подбираем alpha, чтобы RTP == target_rtp; возвращаем целые веса (сумма = WEIGHT_TOTAL)."""

    def weights(alpha):
        raw = [p ** (-alpha) for p in prices]
        s = sum(raw)
        w = [max(1, round(r / s * WEIGHT_TOTAL)) for r in raw]
        # корректируем сумму на самом вероятном предмете
        w[w.index(max(w))] += WEIGHT_TOTAL - sum(w)
        return w

    def rtp(w):
        return sum(wi * pi for wi, pi in zip(w, prices)) / WEIGHT_TOTAL / case_price

    lo, hi = 0.0, 8.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if rtp(weights(mid)) > target_rtp:
            lo = mid
        else:
            hi = mid
    w = weights((lo + hi) / 2)
    return w, rtp(w)


def build():
    """Полный каталог с рассчитанными ценами и весами."""
    out = []
    for ci, case in enumerate(ALL_CASES, start=1):
        prices = item_prices(case)
        weights, rtp = solve_weights(prices, case["price"], case["rtp"])
        items = []
        for i, (raw, price, w) in enumerate(zip(case["items"], prices, weights), start=1):
            if case.get("real"):
                name, _usd, img = raw
                items.append(dict(name=name, icon="", price=price, weight=w, rarity=rarity_abs(price),
                                  slug=f"c{ci}_{i}", image=f"/web/{case.get('dir', 'dota')}/{img}.webp"))
            else:
                name, icon, _mult = raw
                items.append(dict(name=name, icon=icon, price=price, weight=w,
                                  rarity=rarity_for(price, case["price"]), slug=f"c{ci}_{i}", image=None))
        out.append({**case, "index": ci, "items": items, "real_rtp": rtp, "slug": f"case{ci}"})
    return out


if __name__ == "__main__":
    for c in build():
        print(f"\n{c['name']} — {c['price']} PTS, RTP {c['real_rtp']*100:.2f}%")
        for it in c["items"]:
            print(f"  {it['name']:<24}{it['rarity']:<10}{it['price']:>8}  {it['weight']/WEIGHT_TOTAL*100:8.4f}%")
