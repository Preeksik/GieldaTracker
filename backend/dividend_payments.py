"""
HossaLab - daty WYPŁATY dywidend (zakładka Dywidendy).

Yahoo podaje datę wypłaty tylko dla spółek z USA. Dla GPW bierzemy ją z tabeli
https://www.biznesradar.pl/dywidendy/ (wszystkie spółki GPW na jednej stronie: ostatni dzień
z prawem do dywidendy, dzień wypłaty, kwota, status: rekomendowana / uchwalona / wypłacona).

Zasady grzecznego pobierania: jedna strona, najwyżej raz na 12 godzin (pamięć podręczna
w data/cache/dividend_payments.json), robots.txt serwisu na to pozwala. Gdy strona nie odpowiada
albo zmieni wygląd - zostaje ostatnia zapisana wersja, a reszta zakładki działa jak dawniej.

Dane z serwisu to informacja pomocnicza - wiążące są komunikaty spółki (ESPI) i KDPW.
"""

import io
import os
import json
import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger("gpw-api")

SOURCE_URL = "https://www.biznesradar.pl/dywidendy/"
SOURCE_NAME = "BiznesRadar"
CACHE_HOURS = 12
USER_AGENT = "Mozilla/5.0 (HossaLab - prywatny program do portfela)"
MONTHS = {"sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
          "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "lis": 11, "gru": 12}
STATUSES = {"wypłacona", "uchwalona", "rekomendowana"}


def parse_pl_date(text):
    """'01 paź 26' -> '2026-10-01'. 'bd.' / puste -> None."""
    try:
        d, m, y = str(text).strip().split()
        return date(2000 + int(y) if len(y) == 2 else int(y), MONTHS[m.lower()], int(d)).isoformat()
    except (ValueError, KeyError):
        return None


def _col(columns, *words):
    for c in columns:
        low = str(c).lower()
        if all(w in low for w in words):
            return c
    return None


def parse_table(html):
    """Tabela z BiznesRadar -> lista wierszy. Pusta lista, gdy strona zmieniła wygląd."""
    import pandas as pd
    try:
        tables = pd.read_html(io.StringIO(html), flavor="lxml")   # bez zapasowego parsera (html5lib nie jest instalowany)
    except Exception:
        return []
    for t in tables:
        c_prof = _col(t.columns, "profil")
        c_pay = _col(t.columns, "wypłaty")
        c_right = _col(t.columns, "z prawem")
        if not (c_prof and c_pay and c_right):
            continue
        c_amt, c_stat, c_year = _col(t.columns, "na akcj"), _col(t.columns, "status"), _col(t.columns, "za rok")
        rows = []
        for _, r in t.iterrows():
            symbol = str(r[c_prof]).split()[0].strip().upper() if str(r[c_prof]).strip() else ""
            if not symbol or symbol == "NAN":
                continue
            status = str(r[c_stat]).strip().lower() if c_stat is not None else ""
            rows.append({
                "symbol": symbol,
                "payment_date": parse_pl_date(r[c_pay]),
                "last_with_right": parse_pl_date(r[c_right]),
                "amount": str(r[c_amt]).strip() if c_amt is not None and str(r[c_amt]) not in ("nan", "bd.") else None,
                "status": status if status in STATUSES else None,
                "for_year": str(r[c_year]).strip() if c_year is not None else None,
            })
        return rows
    return []


def _cache_file():
    from paths import cache_path
    return cache_path("dividend_payments.json")


def load_rows(fetch_fn=None, cache_file=None, now=None):
    """
    Wiersze z pamięci podręcznej (świeższe niż 12 h) albo pobrane na nowo.
    Zwraca (wiersze, informacja o źródle). Nigdy nie rzuca wyjątkiem.
    """
    now = now or datetime.now()
    cache_file = cache_file or _cache_file()
    cached = None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if cached:
        try:
            age = now - datetime.fromisoformat(cached["fetched_at"])
            if age < timedelta(hours=CACHE_HOURS):
                return cached["rows"], {"name": SOURCE_NAME, "url": SOURCE_URL, "fetched_at": cached["fetched_at"]}
        except (KeyError, ValueError):
            pass

    def default_fetch():
        import requests
        r = requests.get(SOURCE_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        return r.text

    try:
        rows = parse_table((fetch_fn or default_fetch)())
        if not rows:
            raise ValueError("nie znaleziono tabeli dywidend (strona mogła zmienić wygląd)")
        data = {"fetched_at": now.isoformat(timespec="seconds"), "rows": rows}
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(cache_file + ".tmp", cache_file)
        return rows, {"name": SOURCE_NAME, "url": SOURCE_URL, "fetched_at": data["fetched_at"]}
    except Exception as e:
        logger.warning("Daty wypłat dywidend (%s) niedostępne: %s - używam ostatniej zapisanej wersji", SOURCE_NAME, e)
        if cached and cached.get("rows"):
            return cached["rows"], {"name": SOURCE_NAME, "url": SOURCE_URL,
                                    "fetched_at": cached.get("fetched_at"), "stale": True}
        return [], {"name": SOURCE_NAME, "url": SOURCE_URL, "error": str(e)}


def gpw_symbol(ticker):
    """'VOX.WA' -> 'VOX'; spółki spoza GPW -> None."""
    t = (ticker or "").upper()
    return t[:-3] if t.endswith(".WA") else None


def ex_date_after(last_with_right):
    """Dzień odcięcia = pierwsza sesja GPW po ostatnim dniu z prawem do dywidendy (pomija weekendy i święta GPW)."""
    from gpw_calendar import next_session
    return next_session(last_with_right)


def held_at(ticker, ex_date, entries, sales):
    """
    Ile akcji miałeś (z podziałem na konta) w dniu odcięcia dywidendy - prawo do dywidendy daje akcja
    kupiona PRZED dniem odcięcia i sprzedana w dniu odcięcia albo później:
      - transze z portfela kupione przed ex_date,
      - plus transze już sprzedane, ale kupione przed ex_date i sprzedane w ex_date lub później.
    Bez tego wypłata sprzed roku liczyłaby się od akcji dokupionych dopiero wczoraj.
    """
    out = {}

    def add(acc, q):
        acc = (acc or "zwykle").lower()
        out[acc] = out.get(acc, 0.0) + float(q or 0)

    for e in entries or []:
        if e.get("ticker") == ticker and (e.get("buy_date") or "") < ex_date:
            add(e.get("account"), e.get("quantity"))
    for s in sales or []:
        if (s.get("ticker") == ticker and (s.get("buy_date") or "") < ex_date
                and (s.get("sell_date") or "") >= ex_date):
            add(s.get("account"), s.get("quantity"))
    return {a: round(q, 6) for a, q in out.items() if q > 0}


def fix_ex_date(p, mine, today):
    """
    Yahoo w polu exDividendDate trzyma OSTATNIE odcięcie - także sprzed miesięcy, a nawet lat (VGO: 2017).
    Ustawia w pozycji:
      next_ex_date       - tylko przyszłe (albo dzisiejsze) odcięcie; None, gdy nic nie zapowiedziano
      next_ex_source     - 'BiznesRadar' / 'Yahoo'
      last_with_right    - ostatni dzień, w którym kupione akcje dają prawo do dywidendy (z BiznesRadar)
      last_ex_date       - poprzednie odcięcie (do informacji, gdy nowego jeszcze nie ma)
    """
    yahoo_ex = p.get("next_ex_date")
    upcoming = sorted(r["last_with_right"] for r in mine if r.get("last_with_right") and r["last_with_right"] >= today)
    past = [ex_date_after(r["last_with_right"]) for r in mine if r.get("last_with_right") and r["last_with_right"] < today]
    if yahoo_ex and yahoo_ex < today:
        past.append(yahoo_ex)
    p["last_ex_date"] = max(past) if past else None
    p["last_with_right"] = None
    if upcoming:
        p["next_ex_date"], p["next_ex_source"], p["last_with_right"] = ex_date_after(upcoming[0]), SOURCE_NAME, upcoming[0]
    elif yahoo_ex and yahoo_ex >= today:
        p["next_ex_source"] = "Yahoo"
    else:
        p["next_ex_date"], p["next_ex_source"] = None, None


def enrich(positions, rows, today=None):
    """
    Dopisuje do każdej pozycji z /api/portfolio/dividends (i poprawia next_ex_date - patrz fix_ex_date):
      next_payment  - najbliższa wypłata (albo ostatnia, jeśli w tym sezonie już wypłacono): data, status, źródło
      payments      - wszystkie wypłaty z tabeli (np. dwie raty jak w Kętach)
    i do każdej realnej wypłaty z Yahoo (realized_dividends) pole payment_date, gdy da się ją dopasować.
    """
    today = (today or date.today()).isoformat()
    by_symbol = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    for p in positions:
        sym = gpw_symbol(p.get("ticker"))
        fix_ex_date(p, by_symbol.get(sym, []) if sym else [], today)
        mine = sorted([r for r in by_symbol.get(sym, []) if r.get("payment_date")],
                      key=lambda r: r["payment_date"]) if sym else []
        p["payments"] = mine
        nxt = None
        upcoming = [r for r in mine if r["payment_date"] >= today]
        if upcoming:
            r = upcoming[0]
            nxt = {"date": r["payment_date"], "status": r["status"] or "zapowiedziana", "source": SOURCE_NAME,
                   "amount": r["amount"], "last_with_right": r["last_with_right"], "paid": False,
                   "more": len(upcoming) - 1}
        elif mine:
            r = mine[-1]
            nxt = {"date": r["payment_date"], "status": "wypłacona", "source": SOURCE_NAME,
                   "amount": r["amount"], "last_with_right": r["last_with_right"], "paid": True, "more": 0}
        elif p.get("yahoo_payment_date"):
            d = p["yahoo_payment_date"]
            nxt = {"date": d, "status": "wypłacona" if d < today else "zapowiedziana", "source": "Yahoo",
                   "amount": None, "last_with_right": None, "paid": d < today, "more": 0}
        p["next_payment"] = nxt

        # Yahoo zapisuje wypłaty pod datą odcięcia - dopasowujemy dzień wypłaty z tabeli
        for item in p.get("realized_dividends") or []:
            ex = item.get("date")
            hits = []
            for r in mine:
                lw = r.get("last_with_right")
                if lw and ex and lw < ex <= (date.fromisoformat(lw) + timedelta(days=7)).isoformat():
                    hits.append(r["payment_date"])
            if hits:
                item["payment_date"] = hits[0] if len(hits) == 1 else ", ".join(hits)
    return positions


# ============================================================================
# KWOTY NETTO - po podatku, który broker pobiera od razu przy wypłacie
# ============================================================================

PL_TAX = 0.19            # "podatek Belki" od polskich dywidend - XTB pobiera go od razu (konto zwykłe)
FOREIGN_TAX = 0.19       # zagranica: łącznie ok. 19% (część pobiera kraj spółki, resztę rozliczasz w PIT-38)
FOREIGN_TAX_FREE_ACC = 0.15   # IKE/IKZE z zagranicznymi spółkami: zostaje podatek kraju spółki (np. USA 15% z W-8BEN)
TAX_FREE_ACCOUNTS = {"ike", "ikze"}


def _net_factor(ticker, quantities):
    """
    Jaka część brutto zostaje na rękę, uśredniona po kontach (np. część akcji na IKE, część na zwykłym).
    Zwraca (mnożnik, czy to tylko szacunek).
    """
    total = sum(quantities.values())
    if total <= 0:
        return None, False
    polish = gpw_symbol(ticker) is not None
    keep = 0.0
    for acc, q in quantities.items():
        free = acc in TAX_FREE_ACCOUNTS
        if polish:
            rate = 0.0 if free else PL_TAX
        else:
            rate = FOREIGN_TAX_FREE_ACC if free else FOREIGN_TAX
        keep += q * (1 - rate)
    return keep / total, not polish


def _r(x):
    return None if x is None else round(x, 2)


def add_net(positions, entries, summary):
    """
    Dopisuje kwoty netto (po podatku) obok brutto - w tej samej walucie co brutto:
      net_factor, net_is_estimate, tax_note
      realized_dividends[].net_received, realized_total_net_native, realized_total_net_pln
      next_amount_estimate_net_total, annual_estimate_net_pln
    oraz do summary: total_realized_last_12mo_net_pln, total_annual_estimate_net_pln.
    entries - pozycje portfela (potrzebne konto każdej transzy: zwykle / ike / ikze).
    """
    by_ticker = {}
    for e in entries or []:
        acc = (e.get("account") or "zwykle").lower()
        q = by_ticker.setdefault(e.get("ticker"), {})
        q[acc] = q.get(acc, 0) + float(e.get("quantity") or 0)

    tot_real, tot_annual = 0.0, 0.0
    for p in positions:
        qty = by_ticker.get(p.get("ticker"), {})
        f, estimate = _net_factor(p.get("ticker"), qty)
        p["net_factor"], p["net_is_estimate"] = f, estimate
        free = sum(v for a, v in qty.items() if a in TAX_FREE_ACCOUNTS)
        taxed = sum(v for a, v in qty.items() if a not in TAX_FREE_ACCOUNTS)
        if gpw_symbol(p.get("ticker")):
            p["tax_note"] = ("bez podatku (IKE/IKZE)" if taxed == 0 else
                             "po 19% podatku" if free == 0 else
                             f"19% podatku tylko od akcji z konta zwykłego ({taxed:g} z {taxed + free:g})")
        else:
            p["tax_note"] = ("orientacyjnie: łącznie ok. 19% podatku - np. dla USA XTB pobiera 15% przy wypłacie, "
                             "a 4% dopłacasz sam w PIT-38, więc na konto wpływa trochę więcej")
        if f is None:
            continue
        # Realne wypłaty: każda z kontami z dnia odcięcia (held_at), a nie z dzisiejszego portfela
        gross_sum, net_sum = 0.0, 0.0
        for item in p.get("realized_dividends") or []:
            if item.get("total_received") is None:
                continue
            fi = _net_factor(p.get("ticker"), item["accounts"])[0] if item.get("accounts") else f
            item["net_received"] = _r(item["total_received"] * (fi if fi is not None else f))
            gross_sum += item["total_received"]
            net_sum += item["net_received"]
        real_f = (net_sum / gross_sum) if gross_sum else f
        p["realized_total_net_native"] = _r((p.get("realized_total_native") or 0) * real_f)
        p["realized_total_net_pln"] = (_r(p["realized_total_pln"] * real_f)
                                       if p.get("realized_total_pln") is not None else None)
        nxt = p.get("next_amount_estimate_total")
        p["next_amount_estimate_net_total"] = _r(nxt * f) if nxt is not None else None
        ann = p.get("annual_estimate_total_pln")
        p["annual_estimate_net_pln"] = _r(ann * f) if ann is not None else None
        tot_real += p["realized_total_net_pln"] or 0
        tot_annual += p["annual_estimate_net_pln"] or 0
    if summary is not None:
        summary["total_realized_last_12mo_net_pln"] = _r(tot_real)
        summary["total_annual_estimate_net_pln"] = _r(tot_annual)
    return positions
