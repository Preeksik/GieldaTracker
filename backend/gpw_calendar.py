"""
HossaLab - kalendarz sesji GPW (dni wolne od handlu).

GPW nie handluje w weekendy i w dni:
  1 stycznia, 6 stycznia (Trzech Króli), Wielki Piątek, Poniedziałek Wielkanocny,
  1 maja, 3 maja, Boże Ciało (60 dni po Wielkanocy), 15 sierpnia, 1 listopada,
  11 listopada, 24, 25, 26 i 31 grudnia.

Daty Wielkanocy liczymy wzorem (algorytm Meeusa/Jonesa/Butchera), więc kalendarz działa
na każdy rok bez ręcznego dopisywania. Gdyby giełda ogłosiła dodatkowy dzień wolny,
wystarczy dopisać go do EXTRA_CLOSED.

Dotyczy tylko GPW - giełdy w USA mają inne święta (tam nadal pomijamy tylko weekendy).
"""

from datetime import date, timedelta
from functools import lru_cache

EXTRA_CLOSED = set()          # np. {"2027-05-04"} - jednorazowe zamknięcia ogłoszone przez GPW
FIXED = [(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 24), (12, 25), (12, 26), (12, 31)]


def easter(year):
    """Niedziela Wielkanocna (kalendarz gregoriański)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=64)
def holidays(year):
    e = easter(year)
    out = {date(year, m, d) for m, d in FIXED}
    out |= {e - timedelta(days=2), e + timedelta(days=1), e + timedelta(days=60)}
    out |= {date.fromisoformat(x) for x in EXTRA_CLOSED if x.startswith(str(year))}
    return frozenset(out)


def _d(x):
    return date.fromisoformat(x) if isinstance(x, str) else x


def is_session(d):
    d = _d(d)
    return d.weekday() < 5 and d not in holidays(d.year)


def next_session(d):
    """Pierwsza sesja PO dniu d (np. dzień odcięcia = sesja po ostatnim dniu z prawem do dywidendy)."""
    d = _d(d) + timedelta(days=1)
    while not is_session(d):
        d += timedelta(days=1)
    return d.isoformat()


def prev_session(d):
    """Ostatnia sesja PRZED dniem d (np. ostatni dzień, w którym kupione akcje dają dywidendę)."""
    d = _d(d) - timedelta(days=1)
    while not is_session(d):
        d -= timedelta(days=1)
    return d.isoformat()
