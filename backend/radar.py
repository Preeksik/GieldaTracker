"""
HossaLab - Radar: gorące spółki wykrywane z SYGNAŁÓW, a nie z wybranej listy.

Stary Radar działał od strony watchlisty: wpisujesz ticker, a on szuka dla niego
wydarzeń. Ten działa odwrotnie - zbiera zdarzenia z całego rynku i to one przynoszą
spółki. Spółka, o której nikt nie słyszał, trafia na szczyt, bo prezes kupił akcje
za milion albo ogłoszono na nią wezwanie.

ŹRÓDŁA (wszystkie darmowe i udostępniane właśnie po to, żeby je czytać):

  Polska
  - Bankier.pl RSS "Komunikaty ze spółek - ESPI": raporty bieżące wszystkich spółek.
    Tędy przechodzą powiadomienia o transakcjach osób z zarządu (art. 19 MAR),
    wezwania, skupy akcji, znaczące umowy, szacunkowe wyniki.
  - Bankier.pl RSS "Giełda": najważniejsze wiadomości rynkowe dnia.
  - Google News RSS - kilka zapytań tematycznych (MAR, wezwania, skupy, rekomendacje,
    szacunkowe wyniki), które łapią przedruki z Parkietu, PAP Biznes, Strefy Inwestorów.

  USA
  - SEC EDGAR, raporty Form 4: oficjalne zgłoszenia transakcji osób z zarządu.
    Liczymy tylko kod "P" - zakup akcji na rynku za własne pieniądze. Przydziały
    opcji (M), podatki (F) czy darowizny nie mówią nic o przekonaniu insidera.
  - ApeWisdom: liczba wzmianek na Reddicie (WallStreetBets, r/stocks, r/investing)
    i 4chanie /biz w ciągu 24 godzin, z porównaniem do dnia wcześniej.

CZEGO TU NIE MA I DLACZEGO:
  - X.com: darmowego API już nie ma, czytanie kosztuje 0,005 $ za post, a scrapowanie
    łamie regulamin i wymaga logowania.
  - Transakcje polityków: w USA ujawniane z opóźnieniem do 45 dni (STOCK Act), więc
    to z definicji nie jest sygnał "gorący teraz"; w Polsce istnieją tylko roczne
    oświadczenia majątkowe.

JAK LICZY "TEMPERATURĘ":
  Każdy sygnał ma wagę (wezwanie > zakup prezesa > skup akcji > umowa > ...),
  która wygasa z wiekiem. Do tego premie: kilku insiderów kupujących tę samą spółkę
  (klaster), potwierdzenie z kilku niezależnych źródeł, anomalia wolumenu na giełdzie
  i zbliżający się raport. Każdy punkt ma uzasadnienie widoczne w interfejsie.
"""

import os
import re
import json
import html
import math
import time
import logging
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from pydantic import BaseModel

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(BASE_DIR, "radar_store.json")

RETENTION_DAYS = 14
COLLECT_EVERY_MINUTES = 20
MARKET_TTL = 3600
EARNINGS_TTL = 12 * 3600
NAME_RETRY_DAYS = 7
MAX_YAHOO_LOOKUPS = 15
MAX_SEC_FILINGS = 60
SEC_DELAY = 0.2            # 5 zapytań/s - połowa limitu SEC (10/s)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*",
}

BANKIER_FEEDS = {
    "bankier_espi": ("https://www.bankier.pl/rss/espi.xml", "Bankier · komunikaty ESPI"),
    "bankier_gielda": ("https://www.bankier.pl/rss/gielda.xml", "Bankier · giełda"),
}

GOOGLE_QUERIES = [
    ('"art. 19 MAR"', "insider"),
    ('wezwanie do zapisywania się na sprzedaż akcji', "wezwanie"),
    ('"skup akcji własnych"', "skup"),
    ('rekomendacja "cena docelowa" akcje', "rekomendacja"),
    ('"szacunkowe wyniki"', "wyniki_wstepne"),
]

SEC_ATOM = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb="
            "&owner=include&start=0&count=100&output=atom")
APE_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"

# ETF-y i instrumenty indeksowe - są "gorące" na Reddicie codziennie, ale nie są spółkami.
APE_SKIP = {
    "SPY", "QQQ", "IWM", "VOO", "VTI", "DIA", "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "UPRO",
    "UVXY", "VXX", "VIX", "SOXL", "SOXS", "TLT", "GLD", "SLV", "ARKK", "XLF", "XLE", "SMH",
    "TSLL", "NVDL", "SDS", "SH", "PSQ", "IBIT", "USO", "HYG", "EEM", "EFA", "KRE", "XLK",
}


# ============================================================================
# POMOCNICZE
# ============================================================================

def _now():
    return datetime.now(timezone.utc)


def _parse_date(value):
    """Daty z RSS (RFC 822), z EDGAR (ISO) i z naszego magazynu (ISO z strefą)."""
    if not value:
        return None
    value = str(value).strip()
    dt = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm(text):
    """'Żabka Polska S.A.' -> 'zabka polska sa' (ł nie rozkłada się przez NFKD)."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ł", "l").replace("Ł", "L")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()


LEGAL_SUFFIX = re.compile(
    r"[\s,]*(spółka akcyjna|spolka akcyjna|s\.\s?a\.?|sa|se|asa|ag|n\.?v\.?|plc|ltd\.?|inc\.?|"
    r"corp\.?|co\.?|s\.k\.a\.?|sp\.\s?z\s?o\.o\.?)\s*$",
    re.IGNORECASE,
)


def display_name(company):
    """'ENERGA S.A.' -> 'ENERGA'; 'INPRO SPÓŁKA AKCYJNA' -> 'INPRO'."""
    name = (company or "").strip(" -–—:,")
    for _ in range(2):   # "XYZ S.A. SPÓŁKA AKCYJNA" zdarza się rzadko, ale się zdarza
        name = LEGAL_SUFFIX.sub("", name).strip(" -–—:,")
    return name


def looks_like_company(text, loose=False):
    """
    Czy fragment przed ':' to nazwa spółki, a nie zwykły początek zdania?
    Pewne: przyrostek prawny albo WIELKIE LITERY (styl ESPI).
    loose=True: także "Mostostal Zabrze" (każde słowo wielką literą, max 4 słowa) -
    ale taki kandydat musi się potem przełożyć na prawdziwy ticker, inaczej odpada
    (inaczej "Wall Street: spadki" stałoby się spółką).
    """
    t = (text or "").strip()
    if not t or len(t) > 70:
        return False
    if LEGAL_SUFFIX.search(t):
        return True
    letters = [c for c in t if c.isalpha()]
    upper = sum(1 for c in letters if c.isupper())
    if len(letters) >= 2 and upper / len(letters) >= 0.7:
        return True
    if loose:
        words = t.split()
        return 1 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w[:1].isalpha())
    return False


def split_espi_title(title, strict=False, loose=False):
    """
    'ENERGA S.A.: Oddalenie powództwa...' -> ('ENERGA S.A.', 'Oddalenie powództwa...')
    'INPRO SPÓŁKA AKCYJNA - Powiadomienia...' -> ('INPRO SPÓŁKA AKCYJNA', 'Powiadomienia...')
    strict=True (Google News) wymaga, żeby przedrostek naprawdę wyglądał na nazwę spółki.
    """
    t = (title or "").strip()
    m = re.match(r"^(.{2,70}?)\s*:\s+(.+)$", t)
    if m and (not strict or looks_like_company(m.group(1), loose=loose)):
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^(.{2,70}?)\s+[-–—]\s+(.+)$", t)
    if m and looks_like_company(m.group(1)):
        return m.group(1).strip(), m.group(2).strip()
    return None, t


# ============================================================================
# KLASYFIKACJA TREŚCI
# ============================================================================
#
# (tag, etykieta, waga, wydźwięk, wzorzec na tekście po norm()).
# Waga sygnału = najwyższa z pasujących reguł - nie sumujemy, żeby jeden komunikat
# pasujący do trzech wzorców nie liczył się potrójnie.

RULES = [
    ("wezwanie", "Wezwanie na akcje", 35, 1,
     r"wezwani\w* do (zapisywania|sprzedazy)|oglosz\w* wezwani|wezwani\w* na (sprzedaz )?akcj|"
     r"przymusow\w* wykup|squeeze.?out"),
    ("insider", "Transakcja osoby z zarządu (art. 19 MAR)", 14, 0,
     r"\b19\b.{0,12}\bmar\b|obowiazki zarzadcze|pelniac\w* obowiazk|transakcj\w* na akcjach|"
     r"osob\w* blisko zwiazan|(powiadomieni|zawiadomieni|informacj)\w* o transakcj"),
    # Art. 69 ustawy o ofercie: ktoś przekroczył 5/10/15/20/25/33/50/75/90% głosów.
    # To dosłownie sygnał "ktoś zaczął mocno kupować" (albo sprzedawać).
    ("akcjonariusz", "Zmiana udziału dużego akcjonariusza", 12, 0,
     r"zmian\w* (stanu posiadania|udzial)|przekroczeni\w* progu|\bart\w* 69\b|"
     r"znaczn\w* pakiet|zejsci\w* ponizej progu|stan\w* posiadania akcji"),
    ("skup", "Skup akcji własnych", 12, 1,
     r"skup\w* akcji wlasnych|nabyci\w* akcji wlasnych|buyback"),
    ("wyniki_wstepne", "Szacunkowe wyniki", 12, 0,
     r"szacunkow\w* wynik|wstepn\w* wynik|szacunkow\w* (dane|skonsolidowan)"),
    ("rekomendacja", "Rekomendacja / cena docelowa", 10, 0,
     r"rekomendacj|cen\w* docelow|podni\w*sl\w* (cene|rekomendacj)|obniz\w* (cene|rekomendacj)"),
    ("umowa", "Znacząca umowa / kontrakt", 10, 1,
     r"znaczac\w* umow|zawarci\w* umow|kontrakt|zamowieni\w*|list\w* intencyjn|porozumieni"),
    ("upadlosc", "Upadłość / restrukturyzacja", 10, -1,
     r"upadlosc|upadlosci|restrukturyzac|sanacj|niewyplacaln"),
    ("wyniki", "Wyniki finansowe", 8, 0,
     r"wyniki finansow|raport\w* (kwartaln|polroczn|roczn)|\bp sr\b|\bq sr\b|\bpsr\b|\bqsr\b|formularz raportu"),
    ("emisja", "Emisja akcji", 8, -1,
     r"emisj\w* akcji|podwyzszeni\w* kapitalu|oferta publiczn|bookbuilding|przyspieszon\w* budow"),
    ("dywidenda", "Dywidenda", 6, 1, r"dywidend"),
    ("zarzad", "Zmiany we władzach", 4, 0,
     r"prezes|powolani\w* (do )?zarzad|odwolani|rezygnacj|czlonk\w* (zarzadu|rady)"),
]

# "podni[oe]sl": podniósł / podniosła / podnieśli (po norm() bez ogonków).
POSITIVE = re.compile(r"wzrost|rosnie|rajd|zwyzk|szybuj|rekord|wystrzel|podni[oe]sl|lepsze niz|zysk wzrosl|poprawa")
NEGATIVE = re.compile(r"spad|tapniec|przecen|runal|zjazd|strata|gorsze niz|rozczarow|obniz|kary|afera")


def classify(text, hint=None):
    """Zwraca (tagi, etykieta_główna, waga, wydźwięk)."""
    n = norm(text)
    tags, best = [], None
    for tag, label, weight, sent, pattern in RULES:
        if re.search(pattern, n):
            tags.append(tag)
            if best is None or weight > best[2]:
                best = (tag, label, weight, sent)
    if hint and hint not in tags:
        # Zapytanie tematyczne (np. o wezwania) samo w sobie mówi, o czym jest wynik -
        # ale tylko jako podpowiedź o niższej wadze, bo Google zwraca też luźne dopasowania.
        rule = next((r for r in RULES if r[0] == hint), None)
        if rule:
            tags.append(hint)
            if best is None:
                best = (hint, rule[1], max(4, rule[2] // 2), rule[3])
    sentiment = best[3] if best else 0
    if sentiment == 0:
        if NEGATIVE.search(n):
            sentiment = -1
        elif POSITIVE.search(n):
            sentiment = 1
    if best is None:
        return tags, None, 0, sentiment
    return tags, best[1], best[2], sentiment


# ============================================================================
# PARSERY (czyste funkcje - testowane na prawdziwych formatach)
# ============================================================================

def parse_rss(content):
    """RSS 2.0 (Bankier, Google News). Google dopisuje ' - Źródło' do tytułu - zdejmujemy to."""
    root = ET.fromstring(content)
    items = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else None
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        desc = html.unescape(it.findtext("description") or "")
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc)).strip()
        items.append({
            "title": title,
            "link": (it.findtext("link") or "").strip(),
            "date": _parse_date(it.findtext("pubDate")),
            "source": source,
            "description": desc[:300],
        })
    return items


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def parse_sec_atom(content):
    """
    Lista najnowszych zgłoszeń Form 4. Każde zgłoszenie występuje w kanale dwa razy
    (raz jako 'Reporting', raz jako 'Issuer') - deduplikujemy po numerze accession.
    """
    root = _strip_ns(ET.fromstring(content))
    out, seen = [], set()
    for entry in root.iter("entry"):
        cat = entry.find("category")
        term = (cat.get("term") if cat is not None else "") or ""
        if term.strip() != "4":
            continue
        link_el = entry.find("link")
        link = link_el.get("href") if link_el is not None else ""
        blob = " ".join(filter(None, [entry.findtext("id"), link, entry.findtext("summary")]))
        acc = re.search(r"(\d{10}-\d{2}-\d{6})", blob)
        cik = re.search(r"/data/(\d+)/", link or "")
        if not acc or not cik or acc.group(1) in seen:
            continue
        seen.add(acc.group(1))
        out.append({"accession": acc.group(1), "cik": str(int(cik.group(1))),
                    "updated": _parse_date(entry.findtext("updated"))})
    return out


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true")


def parse_form4(text):
    """
    Zwraca zakupy rynkowe (kod P) z raportu Form 4 albo None, jeśli ich nie ma.
    Struktura XML zweryfikowana na prawdziwym zgłoszeniu z EDGAR.
    """
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text or "", re.S)
    if not m:
        return None
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        return None

    def v(el, path):
        x = el.find(path) if el is not None else None
        return x.text.strip() if x is not None and x.text else None

    symbol = (v(root, "issuer/issuerTradingSymbol") or "").upper().strip()
    issuer = v(root, "issuer/issuerName") or symbol
    if not symbol or symbol in ("NONE", "N/A", "NA"):
        return None

    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        role = []
        title = v(rel, "officerTitle")
        if _truthy(v(rel, "isOfficer")):
            role.append(title or "członek zarządu")
        if _truthy(v(rel, "isDirector")):
            role.append("członek rady (director)")
        if _truthy(v(rel, "isTenPercentOwner")):
            role.append("akcjonariusz 10%+")
        owners.append({"name": v(ro, "reportingOwnerId/rptOwnerName") or "?",
                       "role": ", ".join(role) or "insider",
                       "officer_title": title or "",
                       "ten_pct": _truthy(v(rel, "isTenPercentOwner"))})

    buys = []
    for t in root.iter("nonDerivativeTransaction"):
        code = v(t, "transactionCoding/transactionCode")
        ad = v(t, "transactionAmounts/transactionAcquiredDisposedCode/value")
        if code != "P" or ad != "A":
            continue
        try:
            shares = float(v(t, "transactionAmounts/transactionShares/value") or 0)
            price = float(v(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        if shares <= 0:
            continue
        buys.append({"date": v(t, "transactionDate/value"), "shares": shares, "price": price,
                     "value": shares * price})
    if not buys:
        return None

    total_shares = sum(b["shares"] for b in buys)
    total_value = sum(b["value"] for b in buys)
    return {
        "symbol": symbol,
        "issuer": issuer,
        "owners": owners,
        "buys": buys,
        "shares": total_shares,
        "value_usd": total_value,
        "avg_price": total_value / total_shares if total_shares else None,
        "date": max((b["date"] for b in buys if b["date"]), default=None),
    }


def parse_ape(payload):
    out = []
    for r in (payload or {}).get("results", []):
        t = (r.get("ticker") or "").upper()
        name = r.get("name") or t
        if not t or t in APE_SKIP or re.search(r"\bETF\b|\bTrust\b|\bIndex\b", name):
            continue
        try:
            out.append({"ticker": t, "name": name,
                        "rank": int(r.get("rank") or 999),
                        "rank_24h_ago": int(r.get("rank_24h_ago") or 999),
                        "mentions": int(r.get("mentions") or 0),
                        "mentions_24h_ago": int(r.get("mentions_24h_ago") or 0),
                        "upvotes": int(r.get("upvotes") or 0)})
        except (TypeError, ValueError):
            continue
    return out


# ============================================================================
# SIŁA SYGNAŁÓW
# ============================================================================

def insider_us_weight(parsed):
    """Waga zakupu insidera z USA: im większa kwota i wyższe stanowisko, tym mocniej."""
    value = parsed["value_usd"]
    w = 30 if value >= 1_000_000 else 22 if value >= 250_000 else 14 if value >= 50_000 else 6
    titles = " ".join(o["officer_title"] for o in parsed["owners"]).lower()
    if re.search(r"\b(ceo|chief executive|cfo|chief financial|president|chair)", titles):
        w += 6
    if any(o["ten_pct"] for o in parsed["owners"]):
        w += 4
    return w


def ape_weight(r):
    """
    Reddit: sama popularność to mało - liczy się NAGŁY przyrost wzmianek.
    Sufit ~28 pkt celowo poniżej dużego zakupu insidera (30-40): pieniądze osoby
    z zarządu to mocniejszy sygnał niż komentarze anonimowych użytkowników.
    """
    w = 10 if r["rank"] <= 10 else 6 if r["rank"] <= 30 else 3 if r["rank"] <= 100 else 0
    before = max(r["mentions_24h_ago"], 1)
    growth = r["mentions"] / before
    if r["mentions"] >= 30 and growth >= 2:
        w += 12
    elif r["mentions"] >= 20 and growth >= 1.5:
        w += 6
    if r["rank_24h_ago"] - r["rank"] >= 20:
        w += 6
    return w, growth


def decay(age_hours):
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.7
    if age_hours <= 24 * 7:
        return 0.4
    return 0.2


# ============================================================================
# MAGAZYN
# ============================================================================

_lock = threading.RLock()
_run_lock = threading.Lock()
_store = None


def _default_store():
    return {"signals": {}, "names": {}, "sec_seen": [], "market": {}, "earnings": {},
            "status": {}, "last_run": None}


def _load():
    global _store
    if _store is None:
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _store = _default_store()
            if isinstance(data, dict):
                _store.update(data)
        except (FileNotFoundError, json.JSONDecodeError):
            _store = _default_store()
    return _store


def _persist():
    tmp = STORE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_load(), f, ensure_ascii=False)
    os.replace(tmp, STORE_FILE)


def _add_signal(store, sig):
    """Zwraca True, jeśli sygnał jest nowy. Sygnały z Reddita nadpisujemy (to migawka)."""
    sid = sig["id"]
    is_new = sid not in store["signals"]
    if is_new or sid.startswith("ape:"):
        store["signals"][sid] = sig
    return is_new


# ============================================================================
# ROZPOZNAWANIE SPÓŁEK
# ============================================================================

GENERIC_WORDS = {"grupa", "bank", "polska", "polski", "polskie", "capital", "invest", "holding",
                 "group", "global", "energia", "games", "studio", "media", "system", "systems",
                 "trade", "fund", "real", "estate", "development"}
AMBIGUOUS = {"gpw", "wig", "text", "dom", "bio", "pko", "xtb", "atal", "dino"}


def build_known_names(store, extra=()):
    """
    Lista (ticker, nazwa) znanych spółek - do wyłapywania ich w zwykłych nagłówkach.
    Źródła: uniwersum skanera, symbole wyszukiwane w aplikacji, spółki widziane w ESPI.
    """
    known = {}
    try:
        import screener
        for t, (name, _sector, _tags) in screener.SEED.items():
            known[t] = name
    except Exception:
        pass
    for t, name in extra:
        if t and name:
            known.setdefault(t.upper(), name)
    for rec in store["names"].values():
        if rec.get("ticker") and rec.get("display"):
            known.setdefault(rec["ticker"], rec["display"])
    return list(known.items())


def _name_patterns(name):
    """Wzorce do szukania nazwy w nagłówku. Polskie końcówki: Budimex -> Budimeksu."""
    n = norm(display_name(name))
    if not n or n in AMBIGUOUS:
        return []
    pats = []
    if len(n) >= 5:
        pats.append(n)
    first = n.split(" ")[0]
    if len(first) >= 5 and first not in GENERIC_WORDS and first not in AMBIGUOUS and first != n:
        pats.append(first)
    out = []
    for p in pats:
        stem = p[:-1] if len(p) >= 6 else p
        out.append(re.compile(r"\b" + re.escape(stem)))
    return out


# "Analitycy mBanku podnieśli cenę docelową Budimeksu" jest o Budimeksie, nie o mBanku.
# Nazwa tuż po tych słowach to autor rekomendacji albo komentarza, a nie jej temat.
AUTHOR_CONTEXT = re.compile(r"(analityc\w*|ekonomis\w*|strategi?\w*|\bdm\b|\bbm\b|maklersk\w*|"
                            r"zdaniem|wedlug|wg|raport\w*)\s+$")


def match_known(title, known):
    """Znajduje w tytule nazwy znanych spółek. Zwraca listę (ticker, nazwa)."""
    n = norm(title)
    hits = []
    for ticker, name in known:
        for pat in _name_patterns(name):
            m = pat.search(n)
            if m and not AUTHOR_CONTEXT.search(n[max(0, m.start() - 25):m.start()]):
                hits.append((ticker, name))
                break
    return hits


def resolve_names(store, names, yahoo_fn, known):
    """
    Nazwa spółki z komunikatu ESPI -> ticker .WA. Najpierw znane spółki, potem wyszukiwarka
    Yahoo (maks. 15 nowych nazw na przebieg). Wyniki - także negatywne - trafiają do pamięci,
    żeby nie pytać Yahoo w kółko o spółkę, której tam nie ma.
    """
    by_norm = {norm(display_name(n)): t for t, n in known}
    lookups = 0
    for raw in names:
        disp = display_name(raw)
        key = norm(disp)
        if not key:
            continue
        rec = store["names"].get(key)
        if rec and (rec.get("ticker") or
                    (_now() - (_parse_date(rec.get("ts")) or _now())).days < NAME_RETRY_DAYS):
            continue
        ticker = by_norm.get(key)
        looked_up = False
        if not ticker and yahoo_fn and lookups < MAX_YAHOO_LOOKUPS:
            lookups += 1
            looked_up = True
            try:
                entries = yahoo_fn(disp) or []
            except Exception:
                entries = []
            # Porównanie po CAŁYCH słowach - dopasowanie podciągu łapało np. "ot" w "Rotopino".
            first_word = key.split(" ")[0]
            for e in entries:
                t = (e.get("ticker") or "").upper()
                words = norm(e.get("name")).split()
                if t.endswith(".WA") and (first_word in words or len(entries) == 1):
                    ticker = t
                    break
        # Wynik negatywny zapamiętujemy TYLKO po faktycznym zapytaniu. Inaczej nazwa pominięta
        # przez limit zapytań albo szybki przebieg (bez Yahoo) byłaby blokowana na 7 dni.
        if ticker or looked_up:
            store["names"][key] = {"display": disp, "ticker": ticker, "ts": _now().isoformat()}


def ticker_for(store, company):
    rec = store["names"].get(norm(display_name(company)))
    return rec.get("ticker") if rec else None


# ============================================================================
# ZBIERACZE
# ============================================================================

def _status(ok, new=0, error=None, note=None):
    return {"ok": ok, "new": new, "error": error, "note": note, "at": _now().isoformat()}


def collect_bankier(store, fetch, known):
    """Kanały Bankiera: komunikaty ESPI (wszystkie spółki) i wiadomości giełdowe."""
    statuses, espi_names = {}, []
    for key, (url, label) in BANKIER_FEEDS.items():
        try:
            r = fetch(url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            items = parse_rss(r.content)
        except Exception as e:
            statuses[key] = _status(False, error=f"{type(e).__name__}: {str(e)[:80]}")
            continue
        new = 0
        for it in items:
            if not it["title"]:
                continue
            date = it["date"] or _now()
            if key == "bankier_espi":
                company, subject = split_espi_title(it["title"])
                if not company:
                    continue
                tags, label_, weight, sent = classify(subject)
                espi_names.append(company)
                sig = {"id": f"bk:{it['link'] or it['title']}", "type": "insider_pl" if "insider" in tags else "espi",
                       "market": "PL", "company": display_name(company), "ticker": None,
                       "title": subject, "label": label_ or "Komunikat ESPI", "tags": tags,
                       "weight": weight or 2, "sentiment": sent, "url": it["link"],
                       "source": label, "date": date.isoformat()}
                new += _add_signal(store, sig)
            else:
                tags, label_, weight, sent = classify(it["title"])
                for ticker, name in match_known(it["title"], known) or [(None, None)]:
                    sig = {"id": f"bk:{it['link']}:{ticker or '-'}", "type": "news", "market": "PL",
                           "company": display_name(name) if name else None, "ticker": ticker,
                           "title": it["title"], "label": label_ or "Wiadomość rynkowa", "tags": tags,
                           "weight": weight or 5, "sentiment": sent, "url": it["link"],
                           "source": label, "date": date.isoformat()}
                    new += _add_signal(store, sig)
        statuses[key] = _status(True, new=new)
    return statuses, espi_names


def collect_google(store, fetch, known):
    """Zapytania tematyczne w Google News - przedruki komunikatów z wielu portali naraz."""
    new, errors, names = 0, [], []
    for query, hint in GOOGLE_QUERIES:
        url = (f"https://news.google.com/rss/search?q={quote(query + ' when:3d')}"
               f"&hl=pl&gl=PL&ceid=PL:pl")
        try:
            r = fetch(url, headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            items = parse_rss(r.content)
        except Exception as e:
            errors.append(f"{hint}: {type(e).__name__}")
            continue
        for it in items[:40]:
            date = it["date"] or _now()
            if _now() - date > timedelta(days=RETENTION_DAYS):
                continue
            needs_ticker = False
            company, subject = split_espi_title(it["title"], strict=True)
            if not company:
                # Nagłówek w stylu PAP: "Mostostal Zabrze: wezwanie na 100% akcji".
                # Przyjmujemy warunkowo - liczy się tylko, jeśli nazwa przełoży się na ticker.
                company, subject = split_espi_title(it["title"], strict=True, loose=True)
                needs_ticker = bool(company)
            tags, label, weight, sent = classify(subject if company else it["title"], hint=hint)
            if company:
                targets = [(None, company)]
                names.append(company)
            else:
                targets = match_known(it["title"], known)
            for ticker, name in targets:
                sig = {"id": f"gn:{norm(it['title'])[:120]}:{ticker or norm(name or '')}",
                       "type": "insider_pl" if "insider" in tags else "news",
                       "market": "PL", "company": display_name(name), "ticker": ticker,
                       "title": subject if company else it["title"], "label": label or "Wiadomość",
                       "tags": tags, "weight": weight or 5, "sentiment": sent, "url": it["link"],
                       "source": it["source"] or "Google News", "date": date.isoformat(),
                       "needs_ticker": needs_ticker}
                if needs_ticker:
                    # Jeśli "nazwa" okaże się zwykłym początkiem zdania ("Wall Street: ..."),
                    # strumień pokaże pełny nagłówek zamiast fałszywej spółki.
                    sig["raw_title"] = it["title"]
                new += _add_signal(store, sig)
    ok = len(errors) < len(GOOGLE_QUERIES)
    return _status(ok, new=new, error="; ".join(errors) or None), names


def collect_sec(store, fetch, user_agent, sleep=time.sleep):
    """
    SEC EDGAR Form 4 - tylko nowe zgłoszenia od ostatniego przebiegu, maks. 60 naraz,
    5 zapytań na sekundę. SEC wymaga, żeby każdy program przedstawiał się nazwą i e-mailem.
    """
    if not user_agent:
        return _status(False, note="Wyłączone: dodaj SEC_USER_AGENT do backend/.env "
                                   "(SEC wymaga identyfikacji, np. 'HossaLab Jan Kowalski jan@example.com').")
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    try:
        r = fetch(SEC_ATOM, headers=headers, timeout=20)
        r.raise_for_status()
        filings = parse_sec_atom(r.content)
    except Exception as e:
        return _status(False, error=f"{type(e).__name__}: {str(e)[:80]}")

    seen = set(store["sec_seen"])
    todo = [f for f in filings if f["accession"] not in seen][:MAX_SEC_FILINGS]
    new, buys = 0, 0
    for f in todo:
        acc, cik = f["accession"], f["cik"]
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}.txt"
        sleep(SEC_DELAY)
        try:
            resp = fetch(url, headers=headers, timeout=20)
            resp.raise_for_status()
            parsed = parse_form4(resp.text)
        except Exception:
            logger.warning("Radar: nie udało się pobrać Form 4 %s", acc)
            continue   # nie oznaczamy jako przeczytane - spróbujemy przy następnym przebiegu
        seen.add(acc)
        store["sec_seen"].append(acc)
        if not parsed:
            continue
        buys += 1
        owner = parsed["owners"][0] if parsed["owners"] else {"name": "?", "role": "insider"}
        shares = int(parsed["shares"])
        title = (f"{owner['name'].title()} ({owner['role']}) kupił {shares:,} akcji"
                 f" za ok. ${parsed['value_usd']:,.0f}").replace(",", " ")
        # Świeżość liczymy od ZGŁOSZENIA, nie od transakcji: rynek dowiaduje się o zakupie
        # dopiero z Form 4, który może wpłynąć nawet 2 dni robocze po transakcji.
        date = f["updated"] or _now()
        sig = {"id": f"sec:{acc}", "type": "insider_us", "market": "US",
               "company": parsed["issuer"].title(), "ticker": parsed["symbol"],
               "title": title, "label": "Zakup akcji przez insidera (Form 4)", "tags": ["insider", "zakup"],
               "weight": insider_us_weight(parsed), "sentiment": 1,
               "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm",
               "source": "SEC EDGAR", "date": date.isoformat(),
               "details": {"owner": owner["name"], "role": owner["role"], "shares": shares,
                           "value_usd": round(parsed["value_usd"]), "avg_price": parsed["avg_price"],
                           "transaction_date": parsed["date"]}}
        new += _add_signal(store, sig)
    store["sec_seen"] = store["sec_seen"][-5000:]
    return _status(True, new=new, note=f"przejrzano {len(todo)} nowych Form 4, zakupów rynkowych: {buys}")


def collect_ape(store, fetch):
    """ApeWisdom - wzmianki na Reddicie i 4chanie. Każdy przebieg nadpisuje poprzednią migawkę."""
    rows = []
    try:
        for page in (1, 2):
            r = fetch(APE_URL.format(page=page), headers=BROWSER_HEADERS, timeout=15)
            r.raise_for_status()
            rows += parse_ape(r.json())
    except Exception as e:
        if not rows:
            return _status(False, error=f"{type(e).__name__}: {str(e)[:80]}")
    for sid in [s for s in store["signals"] if s.startswith("ape:")]:
        del store["signals"][sid]
    new = 0
    for r in rows:
        w, growth = ape_weight(r)
        if w <= 0:
            continue
        trend = (f"{r['mentions']} wzmianek w 24 h (dzień wcześniej {r['mentions_24h_ago']}, "
                 f"×{growth:.1f}), miejsce #{r['rank']} (było #{r['rank_24h_ago']})")
        sig = {"id": f"ape:{r['ticker']}", "type": "social", "market": "US",
               "company": r["name"], "ticker": r["ticker"], "title": trend,
               "label": "Szum na Reddicie / 4chanie", "tags": ["social"], "weight": w,
               "sentiment": 0, "url": f"https://apewisdom.io/stocks/{r['ticker']}/",
               "source": "ApeWisdom", "date": _now().isoformat(),
               "details": {k: r[k] for k in ("mentions", "mentions_24h_ago", "rank", "rank_24h_ago")}}
        _add_signal(store, sig)
        new += 1
    return _status(True, new=new, note=f"{len(rows)} spółek w zestawieniu")


# ============================================================================
# POTWIERDZENIE RYNKOWE
# ============================================================================

def market_metrics(closes, volumes):
    """Wolumen z 3 ostatnich sesji względem 60 wcześniejszych + zmiana ceny."""
    px = [c for c in closes if c and not math.isnan(c)]
    vol = [0 if (v is None or (isinstance(v, float) and math.isnan(v))) else v for v in volumes][-len(px):]
    if len(px) < 25 or len(vol) < 25:
        return None
    recent = sum(vol[-3:]) / 3
    base_slice = vol[-63:-3] if len(vol) >= 63 else vol[:-3]
    base = sum(base_slice) / len(base_slice) if base_slice else 0
    return {
        "vol_ratio": round(recent / base, 2) if base > 0 else None,
        "ret_1d": round((px[-1] / px[-2] - 1) * 100, 2),
        "ret_5d": round((px[-1] / px[-6] - 1) * 100, 2) if len(px) > 6 else None,
        "price": round(px[-1], 4),
    }


def fetch_market(store, tickers, download_fn=None):
    """Świeże dane (godzinny cache) - anomalii wolumenu nie widać w danych sprzed 12 godzin."""
    import screener
    if download_fn is None:
        import yfinance as yf
        download_fn = lambda syms: yf.download(syms, period="3mo", interval="1d", group_by="ticker",
                                               auto_adjust=True, progress=False, threads=True)
    now = time.time()
    todo = [t for t in tickers if now - (store["market"].get(t) or {}).get("ts", 0) > MARKET_TTL]
    for i in range(0, len(todo), 25):
        chunk = todo[i:i + 25]
        try:
            df = download_fn(chunk if len(chunk) > 1 else chunk[0])
        except Exception:
            logger.exception("Radar: pobranie notowań nie powiodło się")
            df = None
        for t in chunk:
            m = None
            try:
                data = screener._extract(df, t)
                if data:
                    m = market_metrics(*data)
            except Exception:
                pass
            store["market"][t] = {"ts": now, "m": m}


def fetch_earnings(store, tickers, earnings_fn):
    if not earnings_fn:
        return
    now = time.time()
    for t in tickers:
        if now - (store["earnings"].get(t) or {}).get("ts", 0) < EARNINGS_TTL:
            continue
        try:
            d = earnings_fn(t)
        except Exception:
            d = None
        store["earnings"][t] = {"ts": now, "date": str(d)[:10] if d else None}


# ============================================================================
# RANKING
# ============================================================================

REPRINT_WINDOW_HOURS = 48


def dedupe_reprints(signals, key_fn=lambda s: ""):
    """
    Ten sam komunikat przychodzi z Bankiera i jako przedruk (Parkiet, Strefa Inwestorów)
    przez Google News. Liczymy go RAZ, a pozostałe źródła dopisujemy do "also_in".

    Przedruk rozpoznajemy po początku tytułu (Bankier ucina tytuły "..."). Ale spółki
    publikują wiele komunikatów o IDENTYCZNYM tytule - "Powiadomienie o transakcji na
    akcjach" w poniedziałek i w środę to dwie różne transakcje. Dlatego:
      - dwa sygnały z TEGO SAMEGO źródła nigdy nie są duplikatem,
      - przedruk łączymy z najbliższym w czasie sygnałem z innego źródła (maks. 48 h).
    Źródła pierwotne (ESPI, SEC) idą przed przedrukami z Google News, żeby przedruk
    trafił do właściwego oryginału. Zwraca KOPIE - magazyn zostaje nietknięty.
    """
    ordered = sorted(signals, key=lambda s: (s["id"].startswith("gn:"), s["date"]))
    buckets, unique = {}, []
    for s in ordered:
        s = dict(s)
        fp = (key_fn(s), norm(s["title"])[:32])
        ts = _parse_date(s["date"])
        best, best_gap = None, None
        for u in buckets.get(fp, []):
            if u["source"] == s["source"] or s["source"] in u.get("also_in", []):
                continue
            ut = _parse_date(u["date"])
            gap = abs((ts - ut).total_seconds()) / 3600 if ts and ut else 0
            if gap <= REPRINT_WINDOW_HOURS and (best is None or gap < best_gap):
                best, best_gap = u, gap
        if best is not None:
            best["also_in"] = best.get("also_in", []) + [s["source"]]
            continue
        buckets.setdefault(fp, []).append(s)
        unique.append(s)
    return unique


def _group_key(sig, store):
    t = sig.get("ticker") or (ticker_for(store, sig["company"]) if sig.get("company") else None)
    if t:
        return t, t
    return "name:" + norm(sig.get("company") or ""), None


def stream_view(store, market="all", limit=60):
    """
    Strumień najświeższych newsów (bez Reddita - to migawka, a nie wiadomość).
    - ticker ustalony później z pamięci nazw dopisujemy, żeby było widać, o jaką spółkę chodzi,
    - warunkowa "nazwa", która nie okazała się spółką ("Wall Street: spadki..."), wraca do
      nagłówka, zamiast udawać spółkę,
    - przedruki tego samego komunikatu pokazujemy raz.
    """
    items = []
    for s in store["signals"].values():
        if s["type"] == "social" or (market != "all" and s["market"] != market):
            continue
        s = dict(s)
        if s.get("company") and not s.get("ticker"):
            s["ticker"] = ticker_for(store, s["company"])
        if s.get("needs_ticker") and not s.get("ticker"):
            s["title"] = s.get("raw_title") or f"{s['company']}: {s['title']}"
            s["company"] = None
        items.append(s)
    unique = dedupe_reprints(items, key_fn=lambda s: s.get("ticker") or norm(s.get("company") or ""))
    unique.sort(key=lambda s: s["date"], reverse=True)
    return unique[:limit]


def aggregate(store, market="all", kind="all", limit=40):
    now = _now()
    groups = {}
    for sig in store["signals"].values():
        if not sig.get("company") and not sig.get("ticker"):
            continue
        if market != "all" and sig["market"] != market:
            continue
        key, ticker = _group_key(sig, store)
        if key == "name:":
            continue
        if sig.get("needs_ticker") and not ticker:
            continue   # warunkowa nazwa z nagłówka, która nie okazała się spółką giełdową
        g = groups.setdefault(key, {"key": key, "ticker": ticker, "company": sig.get("company") or ticker,
                                    "market": sig["market"], "signals": [], "score": 0.0, "bonuses": []})
        if sig.get("company") and (not g["company"] or g["company"] == g["ticker"]):
            g["company"] = sig["company"]
        g["signals"].append(dict(sig))   # kopia - dopisujemy wiek i wagę tylko do odpowiedzi

    wanted = {"insider": {"insider_pl", "insider_us"}}.get(kind, {kind})
    out = []
    for g in groups.values():
        # Przedruk komunikatu liczymy raz - inaczej dwa powiadomienia MAR wyglądałyby na cztery.
        sigs = sorted(dedupe_reprints(g["signals"]), key=lambda s: s["date"], reverse=True)
        types = {s["type"] for s in sigs}
        if kind != "all" and not (types & wanted):
            continue
        score = 0.0
        for s in sigs:
            age_h = (now - (_parse_date(s["date"]) or now)).total_seconds() / 3600
            s["age_hours"] = round(age_h, 1)
            s["effective"] = round(s["weight"] * decay(age_h), 1)
            score += s["effective"]

        bonuses = []
        fresh = [s for s in sigs if s["age_hours"] <= 24 * 14]
        us_buyers = {(s.get("details") or {}).get("owner") for s in fresh if s["type"] == "insider_us"}
        if len(us_buyers) >= 2:
            bonuses.append(("Klaster: kupuje kilku insiderów naraz", 15))
        pl_insider = [s for s in fresh if s["type"] == "insider_pl"]
        if len(pl_insider) >= 2:
            bonuses.append((f"{len(pl_insider)} powiadomienia MAR w 2 tygodnie", 8))
        sources = {s["source"] for s in sigs}
        if len(types) >= 3 or (len(types) >= 2 and len(sources) >= 3):
            bonuses.append(("Potwierdzone w kilku niezależnych źródłach", 10))

        m = (store["market"].get(g["ticker"]) or {}).get("m") if g["ticker"] else None
        if m and m.get("vol_ratio"):
            vr = m["vol_ratio"]
            if vr >= 3:
                bonuses.append((f"Wolumen {vr:.1f}× średniej z 3 miesięcy", 18))
            elif vr >= 2:
                bonuses.append((f"Wolumen {vr:.1f}× średniej", 12))
            elif vr >= 1.5:
                bonuses.append((f"Wolumen {vr:.1f}× średniej", 6))
        if m and m.get("ret_5d") is not None and abs(m["ret_5d"]) >= 8:
            bonuses.append((f"Kurs {m['ret_5d']:+.1f}% w 5 sesji", 10 if abs(m["ret_5d"]) >= 15 else 5))

        e = (store["earnings"].get(g["ticker"]) or {}).get("date") if g["ticker"] else None
        days_to = None
        if e:
            ed = _parse_date(e + "T00:00:00+00:00")
            if ed:
                days_to = (ed - now).days
                if 0 <= days_to <= 21:
                    bonuses.append((f"Raport za {days_to} dni ({e})", 8))

        total = score + sum(b for _, b in bonuses)
        sentiment = sum(s.get("sentiment", 0) * s["effective"] for s in sigs)
        out.append({
            "key": g["key"], "ticker": g["ticker"], "company": g["company"], "market": g["market"],
            "score": round(total, 1), "signal_score": round(score, 1),
            "bonuses": [{"label": l, "points": p} for l, p in bonuses],
            "signals": sigs[:12], "signal_count": len(sigs), "types": sorted(types),
            "market_data": m, "earnings": e, "earnings_days": days_to,
            "mood": "pozytywny" if sentiment > 5 else "negatywny" if sentiment < -5 else "mieszany",
        })

    out.sort(key=lambda g: g["score"], reverse=True)
    return out[:limit]


# ============================================================================
# PRZEBIEG ZBIERANIA
# ============================================================================

def _default_fetch(url, headers=None, timeout=15):
    return requests.get(url, headers=headers, timeout=timeout)


def run_collection(fetch=None, yahoo_fn=None, earnings_fn=None, download_fn=None,
                   extra_known_fn=None, sec_user_agent=None, sleep=time.sleep, full=True):
    """
    Przebieg zbierania. full=True: wszystkie źródła + dane rynkowe dla czołówki.
    full=False: tylko kanały Bankiera (co 5 min) - ich RSS trzyma zaledwie 10 ostatnich
    pozycji, więc przy rzadszym odpytywaniu w godzinach szczytu publikacji komunikaty
    by przepadały.

    Pracujemy na KOPII magazynu, bez trzymania blokady w trakcie pobierania (20-60 s).
    Inaczej każde otwarcie Radaru w tym czasie wisiałoby do końca przebiegu.
    Awaria jednego źródła nie zatrzymuje pozostałych - każde ma własny status.
    """
    global _store
    if not _run_lock.acquire(blocking=False):
        return {"skipped": "zbieranie już trwa"}
    try:
        fetch = fetch or _default_fetch
        ua = sec_user_agent if sec_user_agent is not None else os.environ.get("SEC_USER_AGENT", "").strip()
        extra = []
        if extra_known_fn:
            try:
                extra = extra_known_fn() or []
            except Exception:
                extra = []

        with _lock:
            store = json.loads(json.dumps(_load()))       # kopia robocza
        known = build_known_names(store, extra)
        status = dict(store.get("status") or {})

        s, espi_names = collect_bankier(store, fetch, known)
        status.update(s)
        # Najpierw ustalamy tickery spółek z ESPI - dzięki temu te same spółki zostaną
        # rozpoznane w nagłówkach Google News jeszcze w TYM przebiegu, a nie dopiero w następnym.
        resolve_names(store, espi_names, yahoo_fn if full else None, known)
        known = build_known_names(store, extra)
        if full:
            status["google_news"], gnames = collect_google(store, fetch, known)
            status["sec_form4"] = collect_sec(store, fetch, ua, sleep=sleep)
            status["reddit"] = collect_ape(store, fetch)
            resolve_names(store, gnames, yahoo_fn, known)

        cutoff = _now() - timedelta(days=RETENTION_DAYS)
        store["signals"] = {k: v for k, v in store["signals"].items()
                            if (_parse_date(v["date"]) or _now()) >= cutoff}

        if full:
            prelim = aggregate(store, limit=60)
            tickers = [g["ticker"] for g in prelim if g["ticker"]][:40]
            try:
                fetch_market(store, tickers, download_fn)
                status["market"] = _status(True, note=f"notowania dla {len(tickers)} spółek")
            except Exception as e:
                status["market"] = _status(False, error=str(e)[:80])
            fetch_earnings(store, tickers[:20], earnings_fn)
            store["last_run"] = _now().isoformat()

        store["status"] = status
        with _lock:
            _store = store
            _persist()
        return {"status": status, "signals": len(store["signals"])}
    finally:
        _run_lock.release()


# ============================================================================
# ENDPOINTY
# ============================================================================

SOURCE_LABELS = {
    "bankier_espi": "Bankier · komunikaty ESPI",
    "bankier_gielda": "Bankier · giełda",
    "google_news": "Google News · zapytania tematyczne",
    "sec_form4": "SEC EDGAR · Form 4",
    "reddit": "Reddit / 4chan (ApeWisdom)",
    "market": "Notowania (Yahoo Finance)",
}


class ExplainRequest(BaseModel):
    key: str


def setup_radar(app, scheduler=None, ask_fn=None, persona="", markdown_rules="",
                yahoo_fn=None, earnings_fn=None, extra_known_fn=None, download_fn=None, fetch=None):
    from fastapi import HTTPException

    def _run(full=True):
        try:
            return run_collection(fetch=fetch, yahoo_fn=yahoo_fn, earnings_fn=earnings_fn,
                                  download_fn=download_fn, extra_known_fn=extra_known_fn, full=full)
        except Exception:
            logger.exception("Radar: przebieg zbierania nie powiódł się")
            return {"error": "przebieg nie powiódł się"}

    @app.get("/api/radar/hot")
    def radar_hot(market: str = "all", kind: str = "all", limit: int = 40):
        """Ranking gorących spółek + strumień najświeższych sygnałów + status źródeł."""
        with _lock:
            store = _load()
            mk = market if market in ("PL", "US") else "all"
            groups = aggregate(store, market=mk, kind=kind, limit=max(1, min(limit, 80)))
            stream = stream_view(store, market=mk, limit=60)
            status = {k: {**v, "label": SOURCE_LABELS.get(k, k)} for k, v in store.get("status", {}).items()}
            return {"groups": groups, "stream": stream, "status": status,
                    "last_run": store.get("last_run"), "total_signals": len(store["signals"]),
                    "every_minutes": COLLECT_EVERY_MINUTES}

    @app.post("/api/radar/refresh")
    def radar_refresh():
        """Zbiera sygnały teraz, nie czekając na harmonogram (trwa 20-60 s)."""
        return _run()

    @app.post("/api/radar/explain")
    def radar_explain(req: ExplainRequest):
        """AI tłumaczy, co się dzieje ze spółką - WYŁĄCZNIE na podstawie zebranych sygnałów."""
        if not ask_fn:
            raise HTTPException(status_code=500, detail="Brak połączenia z modelem.")
        with _lock:
            group = next((g for g in aggregate(_load(), limit=500) if g["key"] == req.key), None)
        if not group:
            raise HTTPException(status_code=404, detail="Tej spółki nie ma już w radarze.")

        lines = []
        for s in group["signals"]:
            d = (s.get("date") or "")[:16].replace("T", " ")
            extra = ""
            if s.get("details") and s["type"] == "insider_us":
                det = s["details"]
                extra = f" [kupujący: {det.get('owner')}, {det.get('role')}, średnia cena ${det.get('avg_price') or 0:.2f}]"
            lines.append(f"- {d} · {s['source']} · {s['label']}: {s['title']}{extra}")
        m = group.get("market_data") or {}
        mkt = (f"Wolumen z 3 ostatnich sesji: {m.get('vol_ratio')}× średniej z 3 miesięcy; "
               f"zmiana kursu 1 sesja: {m.get('ret_1d')}%, 5 sesji: {m.get('ret_5d')}%; cena {m.get('price')}."
               if m else "Brak danych z notowań.")
        earn = f"Najbliższy raport: {group['earnings']}." if group.get("earnings") else "Termin raportu nieznany."

        prompt = (
            persona
            + f"\nRadar wykrył, że o spółce {group['company']} ({group['ticker'] or 'ticker nieustalony'}) "
            "zrobiło się głośno. Poniżej WSZYSTKIE zebrane sygnały - nie masz innych informacji.\n\n"
            "SYGNAŁY:\n" + "\n".join(lines) + f"\n\nRYNEK: {mkt}\n{earn}\n\n"
            "Odpowiedz krótko, w tej strukturze:\n"
            "## Co się dzieje\n2-3 zdania: co łączy te sygnały i czy to jedna historia, czy kilka niezależnych.\n"
            "## Czy rynek już to wycenił\nOceń na podstawie zmiany kursu i wolumenu. Jeśli kurs już mocno ruszył, "
            "powiedz wprost, że wejście teraz to gonienie ruchu.\n"
            "## Na co patrzeć dalej\nKonkretne zdarzenia i poziomy, które potwierdzą albo obalą tezę.\n"
            "## Ryzyko\nNajważniejsze ryzyko w jednym-dwóch zdaniach.\n\n"
            "ZASADY: nie wymyślaj faktów spoza listy sygnałów. Jeśli z tytułu komunikatu nie wynika, czy "
            "insider kupił czy sprzedał (powiadomienia MAR), napisz to wprost. Transakcje insiderów, "
            "rekomendacje i szum w mediach to wskazówki, nie gwarancje."
            + markdown_rules
            + "\nNa końcu jedna linia kursywą: *Analiza edukacyjna, nie porada inwestycyjna.*"
        )
        return {"key": req.key, "company": group["company"], "explanation": ask_fn(prompt, timeout=60)}

    if scheduler is not None:
        try:
            from apscheduler.triggers.interval import IntervalTrigger
            scheduler.add_job(_run, IntervalTrigger(minutes=COLLECT_EVERY_MINUTES), id="hossalab_radar",
                              replace_existing=True, max_instances=1, coalesce=True,
                              next_run_time=datetime.now() + timedelta(seconds=45))
            # Szybki przebieg tylko dla Bankiera - ich RSS ma 10 pozycji, więc trzeba zaglądać częściej.
            scheduler.add_job(lambda: _run(full=False), IntervalTrigger(minutes=5), id="hossalab_radar_fast",
                              replace_existing=True, max_instances=1, coalesce=True,
                              next_run_time=datetime.now() + timedelta(minutes=3))
        except Exception:
            logger.exception("Radar: nie udało się dodać zadania do harmonogramu")

    logger.info("Radar gotowy (zbieranie co %d min, SEC: %s).", COLLECT_EVERY_MINUTES,
                "włączone" if os.environ.get("SEC_USER_AGENT") else "wyłączone - brak SEC_USER_AGENT")
