# AGENTS.md - kontekst projektu dla asystentów AI (Claude Code, Antigravity i inne)

Ten plik streszcza wcześniejszą pracę (prowadzoną w czacie claude.ai, październik-listopad 2026),
żeby każda nowa sesja - niezależnie od narzędzia - wiedziała, nad czym pracujemy.
To JEDYNE źródło kontekstu: `CLAUDE.md` tylko go importuje. Aktualizuj ten plik, nie twórz kopii. Szczegóły: `README.md` (mapa całego projektu)
i `backend/lab/README.md` (laboratorium QuantPulse).

## Właściciel i sposób współpracy

- Adrian (GitHub: Preeksik), Polak, student. Pisze po polsku, często skrótowo i z literówkami - **odpowiadaj po polsku**.
- Windows 11, PowerShell, VS Code. Dwa komputery: **stacjonarny `E:\gielda`** (główny, tu jest gra na papierze)
  i **laptop `C:\Users\adria\Desktop\GieldaTracker`**. Projekt celowo poza OneDrive (puste JSON-y, wolne testy).
- Dopiero się uczy programowania i finansów. **Tłumacz prostym językiem, bez żargonu** (Sharpe, IC, t-stat, pb
  tylko z wyjaśnieniem). Zaczynaj od wniosku („werdykt w prostych słowach”), szczegóły na końcu.
  Kiedyś powiedział, że „stracił kontrolę nad projektem” przez zbyt techniczne raporty - pilnuj tego.
- Gdy podajesz polecenia, podawaj gotowe do wklejenia w PowerShell, z folderem, w którym trzeba je wpisać.
- To jego pieniądze: nigdy nie mów „kupuj/sprzedaj”. Fakty, ryzyka, decyzja jego. Nie jesteś doradcą inwestycyjnym.
- Zanim dotkniesz dużych plików (`main.py` ~3700 linii, `PortfolioTracker.jsx`), przeczytaj aktualną wersję.
  Woli nowe, osobne moduły + kilka linii do wklejenia niż przepisywanie całych plików.

## Co to za projekt

**HossaLab** (repo `GieldaTracker`, prywatne): osobista aplikacja do portfela (GPW i zagranica, konta
zwykłe/IKE/IKZE, broker XTB), z analizami AI (Gemini) i laboratorium **QuantPulse**.

- `backend/` - FastAPI (Python), port 8000. Start: `cd backend; uvicorn main:app --reload --port 8000`.
- `frontend/` - React + Vite, `npm run dev` (port 5173). `const API_URL = 'http://127.0.0.1:8000'` w komponentach.
- `backend/lab/` - QuantPulse (polecenia `python -m lab.<moduł> ...` z folderu `backend`).
- `quant_engine/` - opcjonalny silnik C++20 (pybind11 + scikit-build-core), moduł `quantpulse`.
- `.venv` w głównym folderze projektu. Python 3.13 (PC) / 3.14 (laptop).

## Konwencje w kodzie

- **Backend:** każdy moduł ma `setup_xxx(app, ...)` wywoływane na końcu `main.py`. Funkcje z `main.py` przekazuj
  jako **lambdy** (`load_portfolio=lambda: load_portfolio()`), bo przekazanie funkcji przy imporcie dawało stare wersje.
- **Pliki danych:** zawsze przez `paths.py`: `data_path("x.json")` (dane użytkownika) i `cache_path(...)` (do odtworzenia).
  Zapisy atomowe (plik `.tmp` + `os.replace`). Przed ryzykowną operacją: `write_safety_snapshot(reason=...)`.
- **Komunikaty, nazwy w UI, docstringi, komentarze: po polsku.** Logger: `logging.getLogger("gpw-api")`.
- **Frontend:** style inline z zmiennymi CSS (`var(--down)`, `var(--up)`, `var(--accent)`, `var(--bg-elevated)`,
  `var(--radius-sm)`, `var(--text-dim)`), klasy `hl-btn`, `hl-panel`, `hl-table`, `hl-xtb-*`. Po zmianie portfela
  wołaj `refreshAll()` (odświeża też wykres przez `historyVersion`).
- Liczby w analizach liczy **kod**, AI tylko je komentuje (tak działa `review.py`).
- Testy laboratorium: `python -m pytest lab\tests -q` (z `backend`). Obecnie 85 testów (2 pomijane bez C++). Testy aplikacji: `python -m pytest tests_app -q`.

## Bezpieczeństwo i zasady (twarde)

- **Nigdy nie commituj** `backend/.env` ani `backend/data/` (są w `.gitignore`). Gdyby klucz API trafił do repo → wymiana klucza.
- Nie scrapujemy X.com. `SEC_USER_AGENT` w `.env` ustawia sam użytkownik. Nie wysyłaj nigdzie jego e-maila.
- Daty wypłat dywidend GPW (Yahoo ich nie ma, zna tylko USA): `dividend_payments.py` czyta jedną stronę biznesradar.pl/dywidendy/ najwyżej raz na 12 h (cache `data/cache/dividend_payments.json`, robots.txt pozwala). stooq.pl odpada - robots.txt zabrania botów.
- Prywatny klucz SSH (`id_ed25519_github`) nigdy nie jest pokazywany ani wysyłany. Git push idzie przez alias
  `github.com-prywatne:Preeksik/GieldaTracker.git`.
- Eksporty z XTB użytkownika są prywatne - nie publikuj ich, nie kopiuj poza projekt.
- Commit/push tylko na prośbę użytkownika.
- Użytkownik używa na zmianę Claude Code i Antigravity. Nie zakładaj, że pamiętasz poprzednią sesję -
  sprawdź `git log` / `git status`, zanim zaczniesz. Po większej zmianie zaktualizuj sekcję „Co dalej” w tym pliku.

## QuantPulse - stan na 9 listopada 2026

Cel: uczciwie sprawdzić, czy model wybiera spółki lepiej niż „kup i trzymaj”. Zasady, których pilnujemy zawsze:
**brak zaglądania w przyszłość** (testy w `test_features.py`), **walk-forward** (nauka tylko na przeszłości,
przerwa H+1 sesji), **koszty XTB w każdym wyniku**, **licznik prób** (`data/lab/trials.json`),
werdykt porównuje z trzymaniem wszystkich spółek (`excess_t`).

Wyniki (najważniejsze wnioski):
- **Handel jednodniowy na GPW nie opłaca się** (`models.py`, `backtest.py`): model ma przewagę przed kosztami, ale GPW
  w ciągu sesji średnio traci (~-10 pb/dzień), a poślizg/limit prowizji XTB (0% do 100k EUR/mies., potem 0.2% min 10 EUR)
  i spread CFD (0.30%) zjadają zysk. Wariant „z nocą” (cc) jest nierealny (kupno po cenie, którą znasz po fakcie).
- **Trzymanie przez miesiąc** (`swing.py`): momentum działa. Wygrywają proste reguły i sieć neuronowa (`neural.py`, napisana
  od zera w numpy: 52 wejścia → 32 → 16 → 1, zespół 3 sieci). Sieć sama „odkryła” momentum.
- **Sejf otwarty i zapieczętowany** (`vault.py`, `data/lab/vault.json`): rok XI 2025 – IX 2026, wszystkie trzy strategie
  zdały: A1 +52%, A2 +46%, A3 +54% vs trzymanie +20% (rok był dobry dla trendów; w złym roku -30..50%).

**Strategie zamrożone - NIE ZMIENIAĆ** (`vault.FROZEN`; każda: 10 płynnych spółek GPW, zmiana co 20 sesji, kupno
na fixingu zamknięcia sesji po wskazaniu, poślizg 0.10%):
- **A1** reguła „najbliżej rocznego szczytu” (`dist_high_250`), **A2** „zwrot 6 miesięcy” (`ret_120`), **A3** sieć neuronowa.
Każda zmiana cech/ustawień = nowa strategia, która wymaga nowego uczciwego testu (na przyszłych miesiącach).

**Gra na papierze** (`paper.py`, dziennik `data/lab/paper_journal.json`, tylko na komputerze stacjonarnym):
start - typy z sesji **2026-11-03**, kupno 2026-11-04. Stały harmonogram co 20 sesji; **pierwsze rozliczenie ok. 2 grudnia 2026**.
Uruchamianie: `python -m lab.paper run` (zaległe miesiące program dorabia sam, oznacza `backfilled`).
**Zakładka QuantPulse** (grupa „Laboratorium”, `QuantPulsePanel.jsx`) robi to bez terminala: `backend/lab_api.py`
(`/api/lab/paper`, `/api/lab/paper/run`, automat w dni robocze 18:30 + zaległe po starcie backendu) woła
`lab/paper_report.refresh()` → blokada `paper.lock`, kopia `paper_journal.backup.json`, raport `paper_status.json`
(zakładka tylko go czyta). Przycisk „Sprawdź na dziś” (`LiveCheck.jsx`, `/api/lab/paper/live`, `lab/paper_live.py`): świeże ceny z Yahoo w kilka sekund, cache 60 s, NIC nie zapisuje. Przy pustym dzienniku (laptop) automat i przycisk nic nie robią - nie zakładają drugiej gry.
Pierwsze typy: A1 BNP, CAR, ALE, MBK, KTY, OPL, BDX, BHW, ALR, PCO · A2 ASB, ALE, PCO, VOX, PGE, ZAB, CAR, DNP, MBK, ACP ·
A3 XTB, PKN, CAR, PCO, ASB, GPW, SNT, PEO, ZAB, DOM.

Pułapki danych (już obsłużone, nie „naprawiaj” ich na nowo):
- Yahoo nie ma historii WIG20/WIG (`WIG20.WA` = 1 dzień, `^WIG20` 404) → rynek PL liczony jako średnia spółek (`synthetic_index`).
- ~23% dni GPW ma fałszywe otwarcie (open = wczorajsze zamknięcie) → `open_suspect`; strategie miesięczne używają zamknięć.
- Yahoo wstawiało wiersze ze świąt GPW (2019-2020, wolumen 0) → kalendarz sesji z dni z handlem.
- Dzisiejsza niedokończona sesja nie jest zapisywana (GPW po 17:30, USA po 22:30 czasu PL).
- Usunięte z listy (brak danych w Yahoo): SPL, CCC, CMR, GEA, TIM, SEN (.WA), BK.

## Środowisko - znane problemy

- **Inteligentna kontrola aplikacji (Windows)** blokuje świeże DLL (pandas, scikit-learn, własny silnik C++).
  Na laptopie wyłączona. Objaw: „DLL load failed ... Zasady kontroli aplikacji zablokowały ten plik”.
- Silnik C++ budowany g++ z MSYS2: `$env:CMAKE_GENERATOR="Ninja"; $env:CC="gcc"; $env:CXX="g++"` i
  `pip install --force-reinstall --no-deps --no-cache-dir .\quant_engine`. `CMakeLists.txt` linkuje biblioteki GCC statycznie
  (bez tego: „DLL load failed: nie można odnaleźć modułu”). Bez silnika `lab/engine.py` liczy w Pythonie.
- Po przeniesieniu folderu trzeba zbudować nowe `.venv` (stare ma zaszyte ścieżki). Wszystkie biblioteki naraz: `pip install -r requirements.txt` z głównego folderu (dołącza `backend/requirements.txt` i `backend/lab/requirements-lab.txt`). APScheduler musi zostać w wersji 3 (`<4`).
- Ostrzeżenia Git „LF will be replaced by CRLF” są niegroźne.

## Co dalej (plan)

1. ~~Zakładka QuantPulse w aplikacji~~ - zrobione 9 XI 2026 (opis wyżej, przy grze na papierze). Testy:
   `lab\tests\test_paper_report.py`, `tests_app\test_lab_api.py` (`python -m pytest tests_app -q` z `backend`).
2. **„Moje pomysły” w Dzienniku porad** - ręczne zapisywanie własnych przeczuć, rozliczane po czasie jak porady AI.
3. Za ok. rok: dane z archiwum Radaru (`data/radar_archive/*.jsonl`, zbierane od XI 2026, gdy backend działa) jako nowe cechy
   → nowa wersja modelu, nowy uczciwy test.
