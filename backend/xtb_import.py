"""
HossaLab - import historii z XTB (CSV albo Excel).

Obsługuje eksport "Historia pozycji zamkniętych" / "Pozycje otwarte" z nowej platformy XTB
(kolumny: Instrument, ID Pozycji, Wolumen, Czas otwarcia, Cena otwarcia, Czas zamknięcia,
Cena zamknięcia, Źródło, Zysk/strata) i starsze eksporty xStation z kolumnami "Data otwarcia".

Co robi poza samym wczytaniem:
  - symbole XTB -> Yahoo (VOX.PL -> VOX.WA, SPCX.US -> SPCX, CNDX.UK -> CNDX.L), bo bez tego
    aplikacja nie pobierze cen ani historii,
  - konto z kolumny "Źródło" (IKE / IKZE), reszta trafia na konto wybrane przy imporcie,
  - ponowny import tego samego pliku NICZEGO nie dubluje,
  - jeśli zamknięta pozycja z pliku wciąż wisi w portfelu jako otwarta (sprzedałeś w XTB,
    a w aplikacji nie kliknąłeś "Sprzedaj"), zostaje zdjęta z portfela - inaczej liczyłaby się
    podwójnie: raz jako trzymana do dziś, raz jako sprzedana.

Funkcje są czyste (bez sieci i plików) - main.py podaje im waluty, nazwy i kursy.
"""

import csv
import io
import re
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

# Sufiks giełdy w XTB -> sufiks w Yahoo Finance
XTB_SUFFIX = {
    "PL": ".WA", "US": "", "UK": ".L", "DE": ".DE", "FR": ".PA", "NL": ".AS", "IT": ".MI",
    "ES": ".MC", "CH": ".SW", "BE": ".BR", "PT": ".LS", "DK": ".CO", "SE": ".ST", "NO": ".OL",
    "FI": ".HE", "CZ": ".PR", "AT": ".VI", "IE": ".IR",
}
YAHOO_SUFFIXES = {"WA", "L", "DE", "PA", "AS", "MI", "MC", "SW", "BR", "LS", "CO", "ST", "OL", "HE", "PR", "VI", "IR"}
TICKER_IN_TEXT = re.compile(r"\b([A-Z0-9][A-Z0-9\-]{0,11}\.(?:" + "|".join(sorted(XTB_SUFFIX)) + r"|WA|L))\b")

EPS = 1e-6


def xtb_to_yahoo(symbol):
    """'VOX.PL' -> 'VOX.WA', 'SPCX.US' -> 'SPCX', 'CNDX.UK' -> 'CNDX.L'. Symbole Yahoo zostają bez zmian."""
    s = (symbol or "").strip().upper()
    m = re.fullmatch(r"([A-Z0-9][A-Z0-9\-]*)\.([A-Z]{1,3})", s)
    if not m:
        return s
    base, suffix = m.groups()
    if suffix in XTB_SUFFIX:
        return base + XTB_SUFFIX[suffix]
    return s


# Sufiksy, których Yahoo NIE używa - takie symbole w zapisanych danych to na pewno format XTB.
# Inne (np. .BE) poprawiamy tylko w świeżo importowanym pliku: u Yahoo .BE to giełda w Berlinie.
SAFE_TO_FIX = {"PL", "US", "UK"}


def fix_saved_ticker(symbol):
    """Poprawka symboli już zapisanych w aplikacji: 'CNDX.UK' -> 'CNDX.L', 'VOX.PL' -> 'VOX.WA'."""
    s = (symbol or "").strip().upper()
    m = re.fullmatch(r"([A-Z0-9][A-Z0-9\-]*)\.([A-Z]{2})", s)
    if m and m.group(2) in SAFE_TO_FIX:
        return xtb_to_yahoo(s)
    return symbol


def ticker_from_cell(text):
    """Symbol z komórki: 'NASDAQ 100 ETF (CNDX.UK)', 'VOX.PL', 'VOX.PL, Voxel SA'. None, gdy brak."""
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\(([^)]+)\)\s*$", t)
    if m and " " not in m.group(1).strip():
        return m.group(1).strip().upper()
    m = TICKER_IN_TEXT.search(t.upper())
    if m:
        return m.group(1)
    if " " not in t and len(t) <= 14:
        return t.upper()
    return None


def name_from_cell(text):
    """'NASDAQ 100 ETF (CNDX.UK)' -> 'NASDAQ 100 ETF'; sam symbol -> None."""
    t = (text or "").strip()
    m = re.search(r"\(([^)]+)\)\s*$", t)
    if m:
        return t[: m.start()].strip() or None
    if ticker_from_cell(t) == t.upper():
        return None
    return t or None


def parse_number(raw):
    """'1 234,56' / '1,234.56' / '26.710' / 26.71 -> float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(raw):
    """'29.10.2025 16:36', '2025-10-29 16:36:00', datetime albo liczba dni Excela -> 'YYYY-MM-DD'."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d")
    if isinstance(raw, (int, float)):
        # Excel bez stylów zapisuje datę jako liczbę dni od 30.12.1899
        if 20000 < raw < 80000:
            return (datetime(1899, 12, 30) + timedelta(days=float(raw))).strftime("%Y-%m-%d")
        return None
    s = str(raw).strip().split(" ")[0].split("T")[0]
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ============================================================================
# CZYTANIE PLIKU -> TABELE
# ============================================================================

def _xlsx_rows_builtin(raw):
    """Minimalny czytnik .xlsx bez dodatkowych bibliotek: wartości komórek, arkusz po arkuszu."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        for name in sheets:
            root = ET.fromstring(z.read(name))
            rows = []
            for row in root.iter("{%s}row" % ns["m"]):
                cells = {}
                for c in row.findall("m:c", ns):
                    ref = c.get("r") or ""
                    col_letters = re.match(r"[A-Z]+", ref)
                    col = 0
                    for ch in (col_letters.group(0) if col_letters else ""):
                        col = col * 26 + (ord(ch) - 64)
                    col = (col - 1) if col else len(cells)
                    t = c.get("t")
                    v = c.find("m:v", ns)
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
                    elif v is not None and v.text is not None:
                        val = v.text
                        if t not in ("str", "b"):
                            try:
                                val = float(val)
                            except ValueError:
                                pass
                    else:
                        val = ""
                    cells[col] = val
                if cells:
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
                else:
                    rows.append([])
            yield rows


def _xlsx_rows(raw):
    try:
        import openpyxl  # dokładniejszy (zna formaty dat), ale opcjonalny
    except ImportError:
        yield from _xlsx_rows_builtin(raw)
        return
    import warnings
    # Bez read_only: pliki XTB nie mają zapisanych wymiarów arkusza i w trybie read_only
    # openpyxl widzi tylko pierwszy wiersz. Eksporty są małe, więc pełny tryb nic nie kosztuje.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    for ws in wb.worksheets:
        yield [["" if v is None else v for v in row] for row in ws.iter_rows(values_only=True)]


def _csv_rows(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1250", errors="replace")
    lines = text.splitlines()
    # Separator wybieramy per linia: eksporty potrafią mieć sekcje z różnymi separatorami
    rows = []
    for line in lines:
        if not line.strip():
            rows.append([])
            continue
        delim = ";" if line.count(";") > line.count(",") else ("\t" if line.count("\t") > line.count(",") else ",")
        rows.append(next(csv.reader([line], delimiter=delim)))
    yield rows


DATE_WORDS = ("data", "czas", "time", "date")
SYMBOL_WORDS = ("symbol", "instrument", "ticker")
QTY_WORDS = ("wolumen", "ilość", "ilosc", "quantity", "volume", "lots")


def _is_header(row):
    cells = [str(c).strip().lower() for c in row]
    has_date = any(any(w in c for w in DATE_WORDS) for c in cells)
    has_sym = any(any(w in c for w in SYMBOL_WORDS) for c in cells)
    has_qty = any(any(w in c for w in QTY_WORDS) for c in cells)
    return has_date and (has_sym or has_qty)


def read_tables(raw, filename=""):
    """Zwraca listę tabel: (nagłówki, [słownik wiersza]). Plik może mieć kilka sekcji/arkuszy."""
    is_xlsx = filename.lower().endswith((".xlsx", ".xlsm")) or raw[:2] == b"PK"
    tables = []
    for rows in (_xlsx_rows(raw) if is_xlsx else _csv_rows(raw)):
        i = 0
        while i < len(rows):
            if not _is_header(rows[i]):
                i += 1
                continue
            header = [str(c).strip() for c in rows[i]]
            body = []
            j = i + 1
            while j < len(rows):
                r = rows[j]
                if not any(str(c).strip() for c in r):
                    break
                first = str(r[0]).strip() if r else ""
                if first.startswith("=") and first.endswith("="):
                    break
                if _is_header(r):
                    break
                body.append({header[k]: (r[k] if k < len(r) else "") for k in range(len(header)) if header[k]})
                j += 1
            tables.append((header, body))
            i = j
    return tables


# ============================================================================
# TABELE -> TRANSAKCJE
# ============================================================================

def _find_col(headers, keywords, exclude=()):
    for h in headers:
        low = h.lower()
        if any(ex in low for ex in exclude):
            continue
        if any(kw in low for kw in keywords):
            return h
    return None


def _account_from_source(value, default):
    v = str(value or "").strip().lower()
    if "ikze" in v:
        return "ikze"
    if re.search(r"\bike\b", v):
        return "ike"
    return default


def _is_position_id(value):
    v = value
    if isinstance(v, float) and v.is_integer():
        return True
    return bool(re.fullmatch(r"\d{6,}(\.0)?", str(v or "").strip()))


def account_currency_from_filename(filename):
    """Eksport XTB nazywa się np. 'PLN_52764377_2006-01-01_2026-10-08.xlsx' - waluta rachunku z przodu."""
    m = re.match(r"^(PLN|EUR|USD|GBP|CZK|HUF)_", (filename or "").strip().split("/")[-1].split("\\")[-1])
    return m.group(1) if m else None


def parse_records(tables, default_account="zwykle"):
    """
    Zwraca (transakcje, błędy, pominięte_short, czy_plik_ma_pozycje_otwarte).

    Transakcja: {kind: open|closed, ticker (Yahoo), xtb_symbol, name, quantity, open_price,
    open_date, close_price, close_date, account, position_id, xtb_profit, purchase_value, sale_value}

    Arkusz "Open Positions" z XTB ma wiersz z sumą dla spółki ("Ambra, 138 szt.") i pod nim
    pojedyncze zakupy, w których kolumna instrumentu zawiera numer pozycji zamiast nazwy.
    Sumy pomijamy (nie mają daty otwarcia), a nazwę bierzemy z nich dla zakupów poniżej.
    """
    records, errors, shorts = [], [], 0
    has_open_table = False
    names_by_symbol = {}
    fallback = None
    for header, rows in tables:
        hs = [h for h in header if h]
        c_ticker = _find_col(hs, ["symbol", "ticker"])
        c_instr = _find_col(hs, ["instrument", "nazwa", "name"])
        c_qty = _find_col(hs, list(QTY_WORDS))
        c_oprice = (_find_col(hs, ["cena otwarcia", "open price", "cena zakupu", "open rate"])
                    or _find_col(hs, ["cena", "price"], exclude=["zamk", "close", "akt", "current", "rynk"]))
        c_odate = (_find_col(hs, ["czas otwarcia", "data otwarcia", "open time", "data zakupu"])
                   or _find_col(hs, list(DATE_WORDS), exclude=["zamk", "close"]))
        c_cprice = _find_col(hs, ["cena zamknięcia", "cena zamkniecia", "close price", "close rate"])
        c_cdate = _find_col(hs, ["czas zamknięcia", "czas zamkniecia", "data zamknięcia", "data zamkniecia", "close time"])
        c_id = _find_col(hs, ["id pozycji", "position id", "nr pozycji"])
        c_source = _find_col(hs, ["źródło", "zrodlo", "source", "product", "produkt", "konto"])
        c_type = _find_col(hs, ["kierunek", "side", "typ", "type"], exclude=["instrument"])
        c_profit = _find_col(hs, ["profit/loss", "zysk/strata", "net profit", "zysk", "profit", "p/l"],
                             exclude=["%", "gross", "brutto"])
        c_buyval = _find_col(hs, ["purchase value", "wartość zakupu", "wartosc zakupu"])
        c_sellval = _find_col(hs, ["sale value", "wartość sprzedaży", "wartosc sprzedazy"])

        if not (c_qty and c_oprice and c_odate) or not (c_ticker or c_instr or fallback):
            continue
        if not (c_cprice or c_cdate):
            has_open_table = True

        for n, row in enumerate(rows, start=2):
            try:
                cell_t = row.get(c_ticker) if c_ticker else None
                cell_i = row.get(c_instr) if c_instr else None
                instr_is_id = cell_i not in (None, "") and _is_position_id(cell_i)
                symbol = ticker_from_cell(str(cell_t)) if cell_t not in (None, "") else None
                if not symbol and cell_i not in (None, "") and not instr_is_id:
                    symbol = ticker_from_cell(str(cell_i))
                symbol = symbol or fallback
                if not symbol:
                    continue
                fallback = symbol
                name = None
                if cell_i not in (None, "") and not instr_is_id:
                    # Z osobną kolumną tickera kolumna instrumentu to zawsze nazwa ("Ambra"),
                    # nawet jednowyrazowa - bez tego "Ambra" brana była za symbol.
                    raw_i = str(cell_i).strip()
                    name = (re.sub(r"\s*\([^)]*\)\s*$", "", raw_i) or None) if c_ticker else name_from_cell(raw_i)
                    if name:
                        names_by_symbol.setdefault(symbol, name)
                name = name or names_by_symbol.get(symbol)

                qty = parse_number(row.get(c_qty))
                oprice = parse_number(row.get(c_oprice))
                odate = parse_date(row.get(c_odate))
                if not qty or qty <= 0 or not oprice or not odate:
                    continue   # wiersze sum, podsumowań i nagłówków sekcji

                side = str(row.get(c_type) or "").strip().lower() if c_type else ""
                if side in ("sell", "short", "sprzedaż", "sprzedaz"):
                    shorts += 1   # krótka sprzedaż - to nie jest posiadanie akcji
                    continue

                cprice = parse_number(row.get(c_cprice)) if c_cprice else None
                cdate = parse_date(row.get(c_cdate)) if c_cdate else None
                closed = bool(cprice and cdate)
                pid = None
                if c_id and row.get(c_id) not in (None, ""):
                    pid = row.get(c_id)
                elif instr_is_id:
                    pid = cell_i
                if isinstance(pid, float) and pid.is_integer():
                    pid = int(pid)
                records.append({
                    "kind": "closed" if closed else "open",
                    "ticker": xtb_to_yahoo(symbol),
                    "xtb_symbol": symbol,
                    "name": name,
                    "quantity": qty,
                    "open_price": oprice,
                    "open_date": odate,
                    "close_price": cprice if closed else None,
                    "close_date": cdate if closed else None,
                    "account": _account_from_source(row.get(c_source) if c_source else "", default_account),
                    "position_id": str(pid).strip() if pid not in (None, "") else None,
                    "xtb_profit": parse_number(row.get(c_profit)) if c_profit else None,
                    "purchase_value": parse_number(row.get(c_buyval)) if c_buyval else None,
                    "sale_value": parse_number(row.get(c_sellval)) if c_sellval else None,
                })
            except Exception as e:   # jeden zły wiersz nie przerywa importu
                errors.append(f"Wiersz {n}: {e}")
    return records, errors, shorts, has_open_table


# ============================================================================
# SCALANIE Z PORTFELEM
# ============================================================================

def _q(x):
    return round(float(x or 0), 6)


def merge(entries, sales, records, currency_fn, name_fn, fx_on_date_fn, has_open_table=False,
          account_currency=None, sync=False, ticker_map=None, now=None):
    """
    Dokłada transakcje do portfela (entries) i sprzedaży (sales) - modyfikuje listy w miejscu.

    ZAMKNIĘTE -> Sprzedaże. Duplikaty liczone na poziomie (spółka, konto, dzień sprzedaży):
      jeśli w aplikacji jest już sprzedaż tej spółki z tego dnia na tę samą łączną ilość, cały dzień
      jest pominięty. Dzięki temu ręczne "Sprzedaj 11,5 szt." zgadza się z dwoma wierszami XTB
      (11 + 0,5), a ponowny import tego samego pliku niczego nie dubluje.
    OTWARTE -> porównanie łącznej ilości na (spółka, konto):
      w aplikacji brak -> dodajemy zakupy z XTB; ilość ta sama -> nic nie ruszamy (Twoje ręczne
      wpisy zostają); ilość inna -> raport różnicy, a z sync=True wpisy aplikacji dla tej spółki
      i konta zastępujemy zakupami z XTB.
    Wartości w PLN (Purchase/Sale Value) bierzemy wprost z XTB, gdy rachunek jest w PLN -
    to faktyczne kwoty po kursach, po których XTB przeliczył transakcję.
    """
    now = now or datetime.now()
    tmap = ticker_map or {}
    norm = lambda t: tmap.get(xtb_to_yahoo(t), xtb_to_yahoo(t))          # symbole z pliku
    norm_saved = lambda t: tmap.get(fix_saved_ticker(t), fix_saved_ticker(t))   # symbole już zapisane
    acc_of = lambda x: x.get("account") or "zwykle"
    cur_cache, name_cache = {}, {}
    res = {"open_added": 0, "closed_added": 0, "duplicates": 0, "in_sync": [], "mismatches": [],
           "replaced": [], "not_in_xtb": [], "partial_days": [], "tickers": set(), "unknown_tickers": set()}

    def currency(t):
        if t not in cur_cache:
            c = currency_fn(t)
            if not c:
                res["unknown_tickers"].add(t)   # Yahoo nie zna symbolu - ceny się nie pobiorą
            cur_cache[t] = c or "PLN"
        return cur_cache[t]

    def name(t, fallback):
        if t not in name_cache:
            name_cache[t] = fallback or name_fn(t) or t
        return name_cache[t]

    for r in records:
        r["ticker"] = norm(r["ticker"])
    # Zapisane wcześniej pozycje w formacie XTB (CNDX.UK) też poprawiamy - inaczej zostałyby
    # uznane za zgodne z plikiem i dalej nie miałyby cen.
    for x in list(entries) + list(sales):
        x["ticker"] = norm_saved(x["ticker"])

    # ---------------- zamknięte ----------------
    closed = [r for r in records if r["kind"] == "closed"]
    days = {}
    for r in closed:
        days.setdefault((r["ticker"], r["account"], r["close_date"]), []).append(r)
    app_days = {}
    for s in sales:
        k = (s["ticker"], acc_of(s), s.get("sell_date"))
        app_days.setdefault(k, []).append(_q(s["quantity"]))

    for key, rows in days.items():
        have = app_days.get(key, [])
        file_sum, app_sum = sum(r["quantity"] for r in rows), sum(have)
        if have and app_sum >= file_sum - 1e-4:
            res["duplicates"] += len(rows)
            continue
        todo = rows
        if have:
            left = Counter(have)
            todo = []
            for r in rows:
                if left[_q(r["quantity"])] > 0:
                    left[_q(r["quantity"])] -= 1
                    res["duplicates"] += 1
                else:
                    todo.append(r)
            res["partial_days"].append(f"{key[0]} {key[2]}: w aplikacji {app_sum:g} szt., w XTB {file_sum:g} szt.")
        for r in todo:
            t = r["ticker"]
            cur = currency(t)
            if account_currency == "PLN" and r.get("purchase_value") and r.get("sale_value"):
                cost, proceeds = r["purchase_value"], r["sale_value"]
            else:
                cost = r["quantity"] * r["open_price"] * (fx_on_date_fn(cur, r["open_date"]) or 1.0)
                proceeds = r["quantity"] * r["close_price"] * (fx_on_date_fn(cur, r["close_date"]) or 1.0)
            sales.append({
                "id": str(uuid.uuid4()), "ticker": t, "name": name(t, r["name"]), "account": r["account"],
                "quantity": r["quantity"], "buy_date": r["open_date"], "buy_price": r["open_price"],
                "buy_currency": cur, "sell_date": r["close_date"], "sell_price": r["close_price"],
                "sell_currency": cur, "cost_pln": round(cost, 2), "proceeds_pln": round(proceeds, 2),
                "realized_profit_pln": round(proceeds - cost, 2),
                "recorded_at": now.strftime("%Y-%m-%d %H:%M"),
                "source": "xtb", "xtb_position_id": r["position_id"], "xtb_profit": r["xtb_profit"],
            })
            res["closed_added"] += 1
            res["tickers"].add(t)

    # ---------------- otwarte ----------------
    xtb_open = {}
    for r in records:
        if r["kind"] == "open":
            xtb_open.setdefault((r["ticker"], r["account"]), []).append(r)
    app_open = {}
    for e in entries:
        app_open.setdefault((e["ticker"], acc_of(e)), []).append(e)

    def add_lot(r):
        t = r["ticker"]
        entries.append({
            "id": str(uuid.uuid4()), "ticker": t, "name": name(t, r["name"]), "quantity": r["quantity"],
            "buy_price": r["open_price"], "currency": currency(t), "account": r["account"],
            "buy_date": r["open_date"], "note": "", "source": "xtb", "xtb_position_id": r["position_id"],
        })
        res["tickers"].add(t)

    for key, lots in xtb_open.items():
        t, acc = key
        mine = app_open.get(key, [])
        x_sum, a_sum = sum(r["quantity"] for r in lots), sum(float(e["quantity"]) for e in mine)
        if not mine:
            for r in lots:
                add_lot(r)
            res["open_added"] += len(lots)
        elif abs(x_sum - a_sum) < 1e-4:
            res["in_sync"].append(t)
        elif sync:
            for e in mine:
                entries.remove(e)
            for r in lots:
                add_lot(r)
            res["open_added"] += len(lots)
            res["replaced"].append({"ticker": t, "account": acc, "app_qty": round(a_sum, 6), "xtb_qty": round(x_sum, 6)})
        else:
            res["mismatches"].append({"ticker": t, "name": name(t, lots[0]["name"]), "account": acc,
                                      "app_qty": round(a_sum, 6), "xtb_qty": round(x_sum, 6)})

    # Spółki w aplikacji, których XTB już nie ma na tym rachunku - tylko raport (mogą być u innego brokera).
    if has_open_table:
        accounts = {r["account"] for r in records}
        for (t, acc), mine in app_open.items():
            if acc in accounts and (t, acc) not in xtb_open:
                res["not_in_xtb"].append({"ticker": t, "account": acc,
                                          "app_qty": round(sum(float(e["quantity"]) for e in mine), 6)})

    res["tickers"] = sorted(res["tickers"])
    res["unknown_tickers"] = sorted(res["unknown_tickers"])
    return res