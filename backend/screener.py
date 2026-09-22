"""
HossaLab - skaner rynku pod profil inwestora.

SKĄD TEN MODUŁ:
Na pytanie "2,5k krótkoterminowo, agresywnie, bez krypto" Doradca zaproponował ATAL -
spokojnego dewelopera. Powód nie leżał w słowach promptu, tylko w danych: model dostał
wyłącznie portfel inwestora (w którym ATAL jest), watchlistę i stan indeksów. Wybrał
z jedynych spółek, jakie miał przed oczami. Żadna zmiana sformułowań tego nie naprawi.

CO ROBI:
1. Z samego briefu (horyzont + ryzyko) wyprowadza PROFIL STRATEGII z liczbowymi
   kryteriami. Inwestor nie musi pisać "szukaj wysokiej zmienności i katalizatora" -
   to wynika z tego, że wybrał "krótko" i "agresywnie".
2. Skanuje uniwersum spółek i ETF-ów, liczy dla każdej zmienność, momentum i płynność.
3. Filtruje i układa kandydatów pod profil, a dla najlepszych dociąga katalizatory:
   najbliższy raport i świeże nagłówki.
4. Po odpowiedzi modelu sprawdza, czy każda propozycja pasuje do profilu.
   Tak samo jak weryfikacja tickerów - kod sprawdza model, nie odwrotnie.

UNIWERSUM:
Lista poniżej jest punktem startowym, uzupełnianym o Twój portfel, watchlistę
i każdy symbol, który kiedykolwiek wyszukałeś. Symbol, dla którego Yahoo nie zwróci
danych, po prostu odpada - nie trafi do modelu jako kandydat.
"""

import os
import re
import json
import math
import time
import logging
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "screener_cache.json")
METRICS_TTL = 12 * 3600
CATALYST_TTL = 6 * 3600
BATCH = 25

# ticker -> (nazwa, sektor, tagi)
SEED = {
    # --- GPW: duże spółki ---
    "PKN.WA": ("Orlen", "energetyka/paliwa", []),
    "PKO.WA": ("PKO BP", "banki", []),
    "PEO.WA": ("Bank Pekao", "banki", []),
    "PZU.WA": ("PZU", "ubezpieczenia", []),
    "KGH.WA": ("KGHM", "surowce", []),
    "CDR.WA": ("CD Projekt", "gry", []),
    "LPP.WA": ("LPP", "handel", []),
    "DNP.WA": ("Dino Polska", "handel", []),
    "ALE.WA": ("Allegro", "e-commerce", []),
    "SPL.WA": ("Santander Bank Polska", "banki", []),
    "MBK.WA": ("mBank", "banki", []),
    "OPL.WA": ("Orange Polska", "telekomunikacja", []),
    "CPS.WA": ("Cyfrowy Polsat", "media/telekomunikacja", []),
    "PGE.WA": ("PGE", "energetyka", []),
    "KTY.WA": ("Grupa Kęty", "przemysł", []),
    "BDX.WA": ("Budimex", "budownictwo", []),
    "JSW.WA": ("JSW", "surowce/węgiel", ["wegiel"]),
    "CCC.WA": ("CCC", "handel", []),
    "KRU.WA": ("Kruk", "finanse", []),
    "ALR.WA": ("Alior Bank", "banki", []),
    "PCO.WA": ("Pepco Group", "handel", []),
    "ZAB.WA": ("Żabka Group", "handel", []),
    "ENA.WA": ("Enea", "energetyka", []),
    "TPE.WA": ("Tauron", "energetyka", []),
    # --- GPW: technologia, gry, biotech (wyższa zmienność) ---
    "TEN.WA": ("Ten Square Games", "gry", []),
    "11B.WA": ("11 bit studios", "gry", []),
    "PLW.WA": ("PlayWay", "gry", []),
    "CRJ.WA": ("Creepy Jar", "gry", []),
    "HUG.WA": ("Huuuge", "gry", []),
    "CIG.WA": ("CI Games", "gry", []),
    "BLO.WA": ("Bloober Team", "gry", []),
    "PCF.WA": ("People Can Fly", "gry", []),
    "RVU.WA": ("Ryvu Therapeutics", "biotechnologia", []),
    "CLN.WA": ("Celon Pharma", "biotechnologia", []),
    "MAB.WA": ("Mabion", "biotechnologia", []),
    "SLV.WA": ("Selvita", "biotechnologia", []),
    "BIO.WA": ("Bioton", "biotechnologia", []),
    "TXT.WA": ("Text (LiveChat)", "technologia", []),
    "ACP.WA": ("Asseco Poland", "technologia", []),
    "DAT.WA": ("DataWalk", "technologia", []),
    "SHO.WA": ("Shoper", "technologia", []),
    "ASB.WA": ("Asbis", "technologia", []),
    "SNT.WA": ("Synektik", "medtech", []),
    "VOX.WA": ("Voxel", "medtech", []),
    "MLS.WA": ("ML System", "technologia/fotowoltaika", []),
    "XTB.WA": ("XTB", "finanse", []),
    "GPW.WA": ("GPW", "finanse", []),
    # --- GPW: deweloperzy i usługi (spokojniejsze) ---
    "DOM.WA": ("Dom Development", "deweloperzy", []),
    "1AT.WA": ("Atal", "deweloperzy", []),
    "BFT.WA": ("Benefit Systems", "usługi", []),
    "APR.WA": ("Auto Partner", "handel", []),
    # --- USA: technologia i półprzewodniki ---
    "NVDA": ("NVIDIA", "półprzewodniki", []),
    "AMD": ("AMD", "półprzewodniki", []),
    "AVGO": ("Broadcom", "półprzewodniki", []),
    "MU": ("Micron", "półprzewodniki", []),
    "ARM": ("Arm Holdings", "półprzewodniki", []),
    "SMCI": ("Super Micro Computer", "sprzęt IT", []),
    "TSLA": ("Tesla", "motoryzacja/technologia", []),
    "PLTR": ("Palantir", "oprogramowanie", []),
    "CRWD": ("CrowdStrike", "cyberbezpieczeństwo", []),
    "META": ("Meta Platforms", "internet", []),
    "NFLX": ("Netflix", "media", []),
    "MRNA": ("Moderna", "biotechnologia", []),
    # --- USA: spółki-pochodne kryptowalut (odfiltrowywane przy "bez krypto") ---
    "MSTR": ("Strategy (MicroStrategy)", "krypto-proxy", ["krypto"]),
    "COIN": ("Coinbase", "krypto-proxy", ["krypto"]),
    "MARA": ("MARA Holdings", "krypto-proxy", ["krypto"]),
    # --- ETF-y szerokiego rynku ---
    "VWCE.DE": ("Vanguard FTSE All-World", "ETF: świat", ["etf"]),
    "IWDA.AS": ("iShares Core MSCI World", "ETF: świat", ["etf"]),
    "EUNL.DE": ("iShares Core MSCI World (Xetra)", "ETF: świat", ["etf"]),
    "CSPX.L": ("iShares Core S&P 500", "ETF: USA", ["etf"]),
    "SXR8.DE": ("iShares Core S&P 500 (Xetra)", "ETF: USA", ["etf"]),
    "CNDX.L": ("iShares NASDAQ 100", "ETF: USA tech", ["etf"]),
}

# Słowa w polu "czego nie chcesz" -> tagi/sektory do odrzucenia.
EXCLUSION_RULES = [
    (r"krypt|crypto|bitcoin|btc", lambda t, s: "krypto" in t),
    (r"w[eę]gl|coal", lambda t, s: "wegiel" in t),
    (r"bank", lambda t, s: s == "banki"),
    (r"deweloper|nieruchom", lambda t, s: s == "deweloperzy"),
    (r"\bgry\b|\bgier\b|gaming|gamedev", lambda t, s: s == "gry"),
    (r"biotech", lambda t, s: s == "biotechnologia"),
    (r"etf", lambda t, s: "etf" in t),
    (r"usa|ameryk|zagranic", lambda t, s: not ticker_is_gpw_sector(t)),
    (r"energetyk|paliw", lambda t, s: s.startswith("energetyka")),
]


def ticker_is_gpw_sector(tags):
    return "gpw" in tags


# ============================================================================
# PROFILE STRATEGII - wyprowadzane z briefu, nie z tekstu inwestora
# ============================================================================
#
# vol_* to roczna zmienność (odchylenie dziennych stóp zwrotu × √252), w procentach.
# Orientacyjnie: szerokie ETF-y 13-18%, duże spółki GPW 22-35%, małe spółki
# technologiczne, gry i biotech 40-90%.

PROFILES = {
    ("krotki", "agresywny"): {
        "label": "Spekulacja pod katalizator",
        "vol_min": 40, "vol_max": None, "catalyst_days": 60, "rank": "vol_catalyst", "allow_etf": False,
        "brief": ("Szukasz spółek o WYSOKIEJ zmienności (co najmniej 40% rocznie), z konkretnym "
                  "wydarzeniem w ciągu ~60 dni (raport, wyniki badań, premiera, decyzja regulatora), "
                  "które może wywołać ruch rzędu kilkunastu-kilkudziesięciu procent. Stabilne spółki "
                  "dywidendowe, deweloperzy i szerokie ETF-y NIE pasują do tego profilu."),
    },
    ("krotki", "zrownowazony"): {
        "label": "Krótki swing z umiarkowanym ryzykiem",
        "vol_min": 22, "vol_max": 50, "catalyst_days": 60, "rank": "momentum", "allow_etf": True,
        "brief": "Spółki w wyraźnym trendzie z umiarkowaną zmiennością i bliskim katalizatorem.",
    },
    ("krotki", "ostrozny"): {
        "label": "Krótki horyzont, ochrona kapitału",
        "vol_min": None, "vol_max": 25, "catalyst_days": None, "rank": "low_vol", "allow_etf": True,
        "brief": ("Krótki horyzont i ostrożny profil słabo się łączą z akcjami - powiedz wprost, że "
                  "lokata, konto oszczędnościowe albo obligacje skarbowe mogą być lepsze."),
    },
    ("sredni", "agresywny"): {
        "label": "Wzrost z wysokim ryzykiem",
        "vol_min": 32, "vol_max": None, "catalyst_days": 180, "rank": "vol_momentum", "allow_etf": False,
        "brief": "Spółki wzrostowe o wysokiej zmienności, z katalizatorem w ciągu pół roku.",
    },
    ("sredni", "zrownowazony"): {
        "label": "Zrównoważony portfel średnioterminowy",
        "vol_min": None, "vol_max": 40, "catalyst_days": None, "rank": "quality", "allow_etf": True,
        "brief": "Mieszanka jakościowych spółek i ETF-ów, zmienność do ok. 40% rocznie.",
    },
    ("sredni", "ostrozny"): {
        "label": "Ostrożny, średni horyzont",
        "vol_min": None, "vol_max": 25, "catalyst_days": None, "rank": "low_vol", "allow_etf": True,
        "brief": "Niska zmienność, przewaga szerokich ETF-ów i dużych, stabilnych spółek.",
    },
    ("dlugi", "agresywny"): {
        "label": "Długoterminowy wzrost",
        "vol_min": None, "vol_max": None, "catalyst_days": None, "rank": "growth", "allow_etf": True,
        "brief": "Spółki i ETF-y wzrostowe (technologia), akceptowalne duże obsunięcia po drodze.",
    },
    ("dlugi", "zrownowazony"): {
        "label": "Rdzeń portfela na lata",
        "vol_min": None, "vol_max": 35, "catalyst_days": None, "rank": "core", "allow_etf": True,
        "brief": "Szerokie ETF-y jako rdzeń, uzupełnione jakościowymi spółkami.",
    },
    ("dlugi", "ostrozny"): {
        "label": "Spokojne budowanie kapitału",
        "vol_min": None, "vol_max": 22, "catalyst_days": None, "rank": "core", "allow_etf": True,
        "brief": "Przede wszystkim szerokie ETF-y o niskiej zmienności.",
    },
}


def get_profile(horizon, risk):
    return PROFILES.get((horizon, risk)) or PROFILES[("dlugi", "zrownowazony")]


# ============================================================================
# CACHE
# ============================================================================

_lock = threading.RLock()
_cache = None


def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
        _cache.setdefault("metrics", {})
        _cache.setdefault("catalysts", {})
    return _cache


def _save_cache():
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_load_cache(), f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        logger.exception("Nie udało się zapisać cache skanera")


def _fresh(entry, ttl):
    return entry and (time.time() - entry.get("ts", 0)) < ttl


# ============================================================================
# METRYKI
# ============================================================================

def compute_metrics(closes, volumes):
    """
    closes, volumes - listy liczb (najstarsze pierwsze). Zwraca słownik metryk albo None.
    Czysta funkcja, bez sieci - dzięki temu da się ją przetestować na liczbach policzonych ręcznie.
    """
    pairs = [(c, v) for c, v in zip(closes, volumes)
             if c is not None and not (isinstance(c, float) and math.isnan(c)) and c > 0]
    if len(pairs) < 25:
        return None
    px = [p[0] for p in pairs]
    vol = [0 if (p[1] is None or (isinstance(p[1], float) and math.isnan(p[1]))) else p[1] for p in pairs]

    rets = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]

    def ann_vol(r):
        if len(r) < 10:
            return None
        m = sum(r) / len(r)
        var = sum((x - m) ** 2 for x in r) / (len(r) - 1)
        return round(math.sqrt(var) * math.sqrt(252) * 100, 1)

    def ret(days):
        if len(px) <= days:
            return None
        return round((px[-1] / px[-1 - days] - 1) * 100, 1)

    last20 = list(zip(px[-20:], vol[-20:]))
    turnover = sum(c * v for c, v in last20) / len(last20) if last20 else 0
    hi = max(px[-252:])
    lo = min(px[-252:])

    return {
        "price": round(px[-1], 4),
        "vol_20": ann_vol(rets[-20:]),
        "vol_90": ann_vol(rets[-90:]),
        "ret_1m": ret(21),
        "ret_3m": ret(63),
        "ret_6m": ret(126),
        "turnover": round(turnover, 0),          # średni dzienny obrót w walucie notowań
        "from_high": round((px[-1] / hi - 1) * 100, 1),
        "from_low": round((px[-1] / lo - 1) * 100, 1),
        "sessions": len(px),
    }


def _extract(df, ticker):
    """Wyciąga kolumny jednego tickera z wyniku yf.download (różne wersje yfinance, różne kształty)."""
    import pandas as pd
    if df is None or getattr(df, "empty", True):
        return None
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = set(df.columns.get_level_values(0))
        lvl1 = set(df.columns.get_level_values(1))
        if ticker in lvl0:
            sub = df[ticker]
        elif ticker in lvl1:
            sub = df.xs(ticker, axis=1, level=1)
        else:
            return None
    else:
        sub = df
    if "Close" not in sub or "Volume" not in sub:
        return None
    sub = sub[["Close", "Volume"]].dropna(subset=["Close"])
    if sub.empty:
        return None
    return sub["Close"].astype(float).tolist(), sub["Volume"].fillna(0).astype(float).tolist()


def fetch_metrics(tickers, download_fn=None, force=False):
    """
    Metryki dla listy tickerów, z cache na 12 godzin. Pobiera hurtem przez yf.download
    (paczki po 25) - pojedyncze zapytanie na ticker to ~70 zapytań do Yahoo na jedno
    pytanie do Doradcy i szybki rate limiting.
    """
    if download_fn is None:
        import yfinance as yf
        download_fn = lambda syms: yf.download(
            syms, period="1y", interval="1d", group_by="ticker",
            auto_adjust=True, progress=False, threads=True,
        )

    out, missing = {}, []
    with _lock:
        cache = _load_cache()["metrics"]
        for t in tickers:
            e = cache.get(t)
            if not force and _fresh(e, METRICS_TTL):
                if e.get("m"):
                    out[t] = e["m"]
            else:
                missing.append(t)

    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        try:
            df = download_fn(chunk if len(chunk) > 1 else chunk[0])
        except Exception:
            logger.exception("Skaner: pobranie paczki nie powiodło się (%s...)", chunk[:3])
            df = None
        for t in chunk:
            m = None
            try:
                data = _extract(df, t)
                if data:
                    m = compute_metrics(*data)
            except Exception:
                logger.exception("Skaner: metryki dla %s", t)
            with _lock:
                # Zapisujemy też porażki (m=None), żeby nie pytać Yahoo co chwilę
                # o symbol, który i tak nie zwraca danych.
                _load_cache()["metrics"][t] = {"ts": time.time(), "m": m}
            if m:
                out[t] = m

    if missing:
        with _lock:
            _save_cache()
    return out


# ============================================================================
# FILTR I RANKING
# ============================================================================

def build_universe(extra_tickers=()):
    """Seed + symbole użytkownika (portfel, watchlista, wyszukiwane)."""
    uni = {}
    for t, (name, sector, tags) in SEED.items():
        tags = list(tags) + (["gpw"] if t.endswith(".WA") else [])
        uni[t] = {"ticker": t, "name": name, "sector": sector, "tags": tags, "seed": True}
    for t, name in extra_tickers:
        t = (t or "").upper()
        if t and t not in uni:
            uni[t] = {"ticker": t, "name": name or t, "sector": "nieznany",
                      "tags": ["gpw"] if t.endswith(".WA") else [], "seed": False}
    return uni


def excluded(item, exclusions_text):
    text = (exclusions_text or "").lower()
    if not text:
        return None
    for pattern, rule in EXCLUSION_RULES:
        if re.search(pattern, text) and rule(item["tags"], item["sector"]):
            return pattern
    return None


def _score(item, rank):
    m = item["m"]
    v = m.get("vol_90") or 0
    r1 = m.get("ret_1m") or 0
    r3 = m.get("ret_3m") or 0
    r6 = m.get("ret_6m") or 0
    cat = item.get("catalyst_days")
    cat_bonus = 25 if (cat is not None and cat <= 60) else (10 if (cat is not None and cat <= 120) else 0)
    if rank == "vol_catalyst":
        return v + cat_bonus + 0.2 * abs(r1)
    if rank == "vol_momentum":
        return v + 0.5 * r3 + cat_bonus * 0.5
    if rank == "momentum":
        return r3 + 0.5 * r1 - 0.2 * v
    if rank == "growth":
        return r6 + 0.5 * r3
    if rank == "quality":
        return r6 - 0.5 * v - 0.3 * abs(min(m.get("from_high") or 0, 0))
    if rank in ("low_vol", "core"):
        return -v + (15 if "etf" in item["tags"] else 0)
    return 0


def fit_check(metrics, profile):
    """Czy dana spółka pasuje do profilu? Zwraca (ok, powód)."""
    if not metrics or metrics.get("vol_90") is None:
        return None, "brak danych o zmienności"
    v = metrics["vol_90"]
    if profile.get("vol_min") and v < profile["vol_min"] * 0.8:
        return False, (f"zmienność {v:.0f}% rocznie - za spokojna na profil "
                       f"„{profile['label']}” (szukamy od {profile['vol_min']}%)")
    if profile.get("vol_max") and v > profile["vol_max"] * 1.2:
        return False, (f"zmienność {v:.0f}% rocznie - za ryzykowna na profil "
                       f"„{profile['label']}” (do {profile['vol_max']}%)")
    return True, f"zmienność {v:.0f}% rocznie - pasuje do profilu"


def _days_until(date_str):
    if not date_str:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(date_str))
    if not m:
        return None
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None
    return (d - datetime.now(timezone.utc)).days


def _catalysts(items, earnings_fn, news_fn, limit=8):
    """Najbliższy raport i świeże nagłówki - tylko dla czołówki, bo to wolne zapytania."""
    def one(item):
        t = item["ticker"]
        with _lock:
            e = _load_cache()["catalysts"].get(t)
        if _fresh(e, CATALYST_TTL):
            return t, e["c"]
        c = {"earnings": None, "earnings_days": None, "news": []}
        try:
            if earnings_fn:
                c["earnings"] = earnings_fn(t)
                c["earnings_days"] = _days_until(c["earnings"])
        except Exception:
            pass
        try:
            if news_fn:
                c["news"] = (news_fn(item["name"], max_items=3) or [])[:3]
        except Exception:
            pass
        with _lock:
            _load_cache()["catalysts"][t] = {"ts": time.time(), "c": c}
        return t, c

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(one, items[:limit]))
    with _lock:
        _save_cache()
    return results


def screen(horizon, risk, amount, exclusions, extra_tickers=(), download_fn=None,
           earnings_fn=None, news_fn=None, owned=(), top=10):
    """
    Główne wejście. Zwraca słownik: profil, kandydaci (z metrykami i katalizatorami),
    odrzuceni (z powodem) i statystyki skanu.
    """
    profile = get_profile(horizon, risk)
    uni = build_universe(extra_tickers)
    metrics = fetch_metrics(list(uni), download_fn=download_fn)
    owned = {t.upper() for t in owned}

    min_turnover = max(100_000, 40 * (amount or 0))
    passed, rejected = [], []

    def evaluate(vol_min, vol_max):
        ok, rej = [], []
        for t, item in uni.items():
            m = metrics.get(t)
            if not m:
                continue
            it = {**item, "m": m, "owned": t in owned}
            why = excluded(it, exclusions)
            if why:
                rej.append((t, "wykluczone przez inwestora"))
                continue
            if "etf" in it["tags"] and not profile["allow_etf"]:
                rej.append((t, "ETF nie pasuje do profilu"))
                continue
            v = m.get("vol_90")
            if v is None:
                continue
            if vol_min and v < vol_min:
                rej.append((t, f"zmienność {v:.0f}% < {vol_min:.0f}%"))
                continue
            if vol_max and v > vol_max:
                rej.append((t, f"zmienność {v:.0f}% > {vol_max:.0f}%"))
                continue
            # Płynność sprawdzamy tylko na GPW - zagraniczne pozycje z listy to duże,
            # bardzo płynne spółki, a obrót w obcej walucie nie jest porównywalny wprost.
            if "gpw" in it["tags"] and (m.get("turnover") or 0) < min_turnover:
                rej.append((t, f"za mała płynność ({m.get('turnover', 0):,.0f} zł dziennie)".replace(",", " ")))
                continue
            ok.append(it)
        return ok, rej

    vmin, vmax = profile.get("vol_min"), profile.get("vol_max")
    passed, rejected = evaluate(vmin, vmax)
    relaxed = False
    if len(passed) < 5 and (vmin or vmax):
        # Gdy kryteria są zbyt ostre na obecny rynek, luzujemy je o 20% - i mówimy o tym
        # modelowi, zamiast udawać, że kandydatów jest dużo.
        passed, rejected = evaluate(vmin * 0.8 if vmin else None, vmax * 1.2 if vmax else None)
        relaxed = True

    passed.sort(key=lambda it: _score(it, profile["rank"]), reverse=True)

    if profile.get("catalyst_days"):
        cats = _catalysts(passed[: top + 4], earnings_fn, news_fn, limit=top + 4)
        for it in passed:
            c = cats.get(it["ticker"])
            if c:
                it["catalysts"] = c
                it["catalyst_days"] = c.get("earnings_days")
        passed.sort(key=lambda it: _score(it, profile["rank"]), reverse=True)

    candidates = [{
        "ticker": it["ticker"], "name": it["name"], "sector": it["sector"],
        "owned": it["owned"], "metrics": it["m"], "catalysts": it.get("catalysts"),
    } for it in passed[:top]]

    return {
        "profile": {k: profile[k] for k in ("label", "brief", "vol_min", "vol_max", "catalyst_days")},
        "relaxed": relaxed,
        "candidates": candidates,
        "scanned": len(metrics),
        "universe": len(uni),
        "rejected_owned": [(t, r) for t, r in rejected if t in owned],
    }


def candidates_block(result):
    """Tekst dla modelu: profil strategii i lista kandydatów z liczbami."""
    p = result["profile"]
    lines = [f"PROFIL STRATEGII (wyprowadzony przez aplikację z horyzontu i apetytu na ryzyko): "
             f"{p['label']}.", p["brief"]]
    if result["relaxed"]:
        lines.append("Uwaga: przy obecnym rynku mało spółek spełniało kryteria, więc skaner poluzował "
                     "je o 20%. Powiedz to inwestorowi.")

    lines.append(f"\nKANDYDACI ZE SKANERA ({len(result['candidates'])} najlepiej dopasowanych "
                 f"z {result['scanned']} przeskanowanych, dane z Yahoo Finance):")
    for c in result["candidates"]:
        m = c["metrics"]
        parts = [f"zmienność {m.get('vol_90')}%/rok",
                 f"1M {m.get('ret_1m'):+}%" if m.get("ret_1m") is not None else None,
                 f"3M {m.get('ret_3m'):+}%" if m.get("ret_3m") is not None else None,
                 f"{m.get('from_high')}% od szczytu 52T" if m.get("from_high") is not None else None,
                 f"cena {m.get('price')}"]
        cat = c.get("catalysts") or {}
        if cat.get("earnings"):
            d = cat.get("earnings_days")
            parts.append(f"najbliższy raport: {cat['earnings']}" + (f" (za {d} dni)" if d is not None and d >= 0 else ""))
        lines.append(f"  - {c['name']} ({c['ticker']}), {c['sector']}"
                     + (" [inwestor już ma]" if c["owned"] else "") + ": "
                     + ", ".join(x for x in parts if x))
        for n in (cat.get("news") or [])[:2]:
            lines.append(f"      nagłówek: {n.lstrip('- ').strip()}")

    if result.get("rejected_owned"):
        lines.append("\nSPÓŁKI Z PORTFELA INWESTORA, KTÓRE NIE PASUJĄ DO TEGO PROFILU (nie proponuj ich):")
        for t, why in result["rejected_owned"]:
            lines.append(f"  - {t}: {why}")

    return "\n".join(lines)
