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


def item_prices(case):
    out = []
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
    for ci, case in enumerate(CASES, start=1):
        prices = item_prices(case)
        weights, rtp = solve_weights(prices, case["price"], case["rtp"])
        items = []
        for i, ((name, icon, mult), price, w) in enumerate(zip(case["items"], prices, weights), start=1):
            items.append(
                dict(name=name, icon=icon, price=price, weight=w,
                     rarity=rarity_for(price, case["price"]), slug=f"c{ci}_{i}")
            )
        out.append({**case, "index": ci, "items": items, "real_rtp": rtp, "slug": f"case{ci}"})
    return out


if __name__ == "__main__":
    for c in build():
        print(f"\n{c['name']} — {c['price']} PTS, RTP {c['real_rtp']*100:.2f}%")
        for it in c["items"]:
            print(f"  {it['name']:<24}{it['rarity']:<10}{it['price']:>8}  {it['weight']/WEIGHT_TOTAL*100:8.4f}%")
