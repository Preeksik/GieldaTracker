# HossaLab (repozytorium GieldaTracker)

Osobista aplikacja do prowadzenia portfela inwestycyjnego: polska giełda i rynki zagraniczne, rachunki
zwykłe, IKE i IKZE, import historii z XTB, analizy AI (Gemini). Do tego **QuantPulse**, czyli laboratorium,
które uczciwie sprawdza, czy da się przewidywać ruchy kursów.

> Wszystko tutaj służy do nauki i porządku we własnych finansach. To **nie są porady inwestycyjne**.
> Aplikacja ani laboratorium **nie łączą się z kontem w XTB** i niczego same nie kupują ani nie sprzedają.

---

## Spis treści

1. [Mapa projektu w jednym rzucie oka](#1-mapa-projektu-w-jednym-rzucie-oka)
2. [Co potrafi aplikacja](#2-co-potrafi-aplikacja)
3. [Uruchomienie krok po kroku (Windows)](#3-uruchomienie-krok-po-kroku-windows)
4. [QuantPulse - laboratorium](#4-quantpulse---laboratorium)
5. [Silnik C++ (opcjonalny)](#5-silnik-c-opcjonalny)
6. [Twoje dane i bezpieczeństwo](#6-twoje-dane-i-bezpieczeństwo)
7. [Codzienne polecenia - ściąga](#7-codzienne-polecenia---ściąga)
8. [Gdy coś nie działa](#8-gdy-coś-nie-działa)

---

## 1. Mapa projektu w jednym rzucie oka

```
GieldaTracker/
├── backend/                  serwer aplikacji (Python, FastAPI) - port 8000
│   ├── main.py               serce backendu: portfel, sprzedaże, watchlista, alerty, dywidendy, kopie
│   ├── paths.py              gdzie leżą pliki z danymi (backend/data)
│   ├── xtb_import.py         import historii z XTB (.xlsx/.csv), w tym korekty "Corr Reopen"
│   ├── review.py             "Przegląd portfela" - liczby liczy kod, AI je komentuje
│   ├── advisor.py            doradca AI (pytania o portfel, plany na spółki)
│   ├── journal.py            Dziennik porad - zapisuje porady AI i rozlicza je po czasie
│   ├── radar.py              Radar gorących spółek (ESPI, wiadomości, insiderzy USA, Reddit) + archiwum
│   ├── broker.py             profil brokera i koszty (XTB) do analiz
│   ├── automation.py         harmonogram: poranny przegląd, skaner ESPI, powiadomienia Telegram
│   ├── ai_models.py          wybór i przełączanie modelu AI
│   ├── search.py             wyszukiwarka spółek (nazwa albo ticker)
│   ├── screener.py           lista spółek do skanera i Radaru
│   ├── portfolio_clear.py    "Wyczyść cały portfel" z kopią zapasową
│   ├── requirements.txt      biblioteki Pythona dla aplikacji
│   ├── .env                  klucze i hasła (NIE trafia na GitHuba)
│   ├── data/                 TWOJE DANE (NIE trafiają na GitHuba) - patrz rozdział 6
│   └── lab/                  QuantPulse - laboratorium (osobne polecenia w terminalu)
│       ├── universe.py       lista spółek (GPW + USA), indeksy, waluty
│       ├── data_store.py     pobieranie 10 lat notowań z Yahoo, kontrola jakości danych
│       ├── features.py       cechy (wskaźniki) i etykiety, czyli to, co model ma przewidzieć
│       ├── engine.py         most do silnika C++ (bez niego liczy Python, te same wyniki)
│       ├── models.py         modele i test walk-forward (handel jednodniowy)
│       ├── backtest.py       symulacja z kosztami XTB (akcje i CFD)
│       ├── swing.py          trzymanie przez tydzień / miesiąc
│       ├── neural.py         sieć neuronowa napisana od zera (numpy)
│       ├── vault.py          sejf - jednorazowy sprawdzian na ostatnim roku
│       ├── paper.py          gra na papierze (typy co miesiąc, rozliczenie)
│       ├── tests/            testy laboratorium
│       └── README.md         szczegółowy opis laboratorium
├── frontend/                 wygląd aplikacji (React + Vite) - http://localhost:5173
│   └── src/                  ekrany i elementy (opis w rozdziale 2)
└── quant_engine/             silnik obliczeń QuantPulse w C++20 (opcjonalny)
```

**Jak to ze sobą rozmawia:** przeglądarka (frontend, port 5173) pyta backend (port 8000) adresami
`/api/...`. Backend czyta i zapisuje pliki w `backend/data`, pobiera ceny z Yahoo Finance, a do analiz
woła Gemini. Laboratorium działa osobno, poleceniami w terminalu, i korzysta z własnego folderu
`backend/data/market` (notowania) oraz `backend/data/lab` (wyniki).

---

## 2. Co potrafi aplikacja

| Funkcja | Co robi | Główne pliki |
|---|---|---|
| **Portfel** | pozycje na kontach zwykłym/IKE/IKZE, aktualne wyceny w PLN, zysk, szacunek podatku Belki, wykres wartości w czasie | `main.py`, `PortfolioTracker.jsx`, `PortfolioHistoryChart.jsx` |
| **Import** | zwykły CSV z pozycjami albo pełna historia z XTB: zamknięte pozycje → Sprzedaże, otwarte porównane z portfelem, ponowny import niczego nie dubluje | `xtb_import.py`, `PortfolioTracker.jsx` |
| **Sprzedaż / usuwanie** | „Sprzedaj” zapisuje transakcję w Sprzedażach. „Usuń” kasuje pomyłkę (z potwierdzeniem). „Wyczyść cały portfel” usuwa wszystko po dwóch potwierdzeniach i robi kopię zapasową | `main.py`, `portfolio_clear.py`, `ClearPortfolio.jsx` |
| **Sprzedaże i PIT-38** | zrealizowane zyski i straty, podsumowanie do zeznania podatkowego | `Saleshistory.jsx` |
| **Przegląd portfela** | dywersyfikacja, koncentracja, porównanie z rynkiem. Liczby liczy kod, AI tylko komentuje | `review.py`, `PortfolioReport.jsx` |
| **Doradca AI** | pytania o portfel i spółki, plan „kup / trzymaj / sprzedaj” | `advisor.py`, `Advisor.jsx` |
| **Dziennik porad** | każda porada AI zapisana z ceną z tamtego dnia, po czasie widać, czy się sprawdziła (w złotówkach, z podziałem na źródła) | `journal.py`, `JournalPanel.jsx` |
| **Radar** | gorące spółki z sygnałów: komunikaty ESPI, wiadomości, zakupy insiderów w USA (SEC), szum na Reddicie. Każdy sygnał trafia też do archiwum dla laboratorium | `radar.py`, `RadarPanel.jsx` |
| **Watchlista i alerty** | obserwowane spółki, alerty cenowe (co 15 minut), katalizatory | `main.py`, `Pricealerts.jsx` |
| **Dywidendy** | kalendarz dywidend i przypomnienia o dniu odcięcia | `DividendCalendar.jsx` |
| **Skaner ESPI** | przegląd nowych komunikatów spółek | `EspiScanner.jsx` |
| **Poranny przegląd** | podsumowanie dnia, może przychodzić na Telegram | `automation.py`, `MorningDigest.jsx`, `AutomationPanel.jsx` |
| **Wiadomości portfela** | newsy o spółkach, które masz | `PortfolioNews.jsx` |
| **Broker** | profil i koszty XTB, brane pod uwagę w analizach | `broker.py`, `BrokerPanel.jsx` |
| **Model AI** | przełączanie modelu Gemini | `ai_models.py`, `ModelSwitcher.jsx` |
| **Kopia zapasowa** | eksport, import i automatyczne kopie przed ryzykownymi operacjami, przywracanie jednym kliknięciem | `main.py`, `BackupPanel.jsx` |

---

## 3. Uruchomienie krok po kroku (Windows)

**Potrzebne raz:** Python 3.13 albo 3.14 (python.org, z zaznaczonym „Add python.exe to PATH”),
Node.js LTS (nodejs.org) i Git.

> Trzymaj projekt **poza OneDrive** (np. `E:\gielda`). OneDrive potrafi zostawiać puste pliki JSON
> i spowalnia wszystko kilkukrotnie.

### Pierwsze uruchomienie

```powershell
cd E:\gielda                                   # główny folder projektu
py -m venv .venv                                # wirtualne środowisko Pythona (raz)
.venv\Scripts\Activate.ps1                      # włączenie - na początku linii pojawi się (.venv)
pip install -r backend\requirements.txt         # biblioteki aplikacji
pip install -r backend\lab\requirements-lab.txt # biblioteki laboratorium (opcjonalnie)
cd frontend
npm install                                     # biblioteki wyglądu (raz)
```

Jeśli PowerShell nie pozwala uruchomić `Activate.ps1`, wpisz raz:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### Plik `backend\.env` (klucze, nigdy na GitHuba)

| Zmienna | Do czego | Wymagana? |
|---|---|---|
| `GOOGLE_API_KEY` | klucz do Gemini (analizy AI) | tak, dla funkcji AI |
| `GEMINI_MODEL` | który model Gemini (domyślnie ustawiony w kodzie) | nie |
| `SEC_USER_AGENT` | identyfikacja przy pobieraniu zgłoszeń insiderów z SEC (USA), np. nazwa aplikacji i Twój e-mail | nie (bez niej Radar pomija SEC) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | powiadomienia na Telegram | nie |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TO` | powiadomienia e-mail | nie |
| `HOSSALAB_TZ` | strefa czasowa harmonogramu (domyślnie Europe/Warsaw) | nie |

### Codzienne uruchamianie: dwa terminale

```powershell
# terminal 1 - backend
cd E:\gielda\backend
..\.venv\Scripts\Activate.ps1
uvicorn main:app --reload --port 8000

# terminal 2 - frontend
cd E:\gielda\frontend
npm run dev
```

Potem otwórz w przeglądarce adres, który pokaże `npm run dev` (zwykle http://localhost:5173).
Gdy backend działa, w tle pracuje też harmonogram: alerty cenowe, Radar, kopie wykresu portfela i poranny przegląd.

---

## 4. QuantPulse - laboratorium

Osobna część projektu, która sprawdza na danych z 10 lat, czy model potrafi wybierać spółki lepiej
niż zwykłe kupienie i trzymanie. Działa wyłącznie w terminalu (z folderu `backend`), na danych z przeszłości.
Szczegóły: [`backend/lab/README.md`](backend/lab/README.md).

### Jak powstawało i co wyszło

| Etap | Co zrobiliśmy | Wynik |
|---|---|---|
| 1. Dane | 10 lat notowań ~180 spółek (GPW + USA), kontrola błędów Yahoo | ✅ |
| 2. Silnik C++ | szybkie statystyki, zgodne z numpy do 1e-10 | ✅ (opcjonalny) |
| 3. Cechy | 20 wskaźników + surowy przebieg ceny, test „bez zaglądania w przyszłość” | ✅ |
| 4–5. Handel jednodniowy | modele + backtest z kosztami XTB (akcje i CFD) | ❌ **nie opłaca się** - koszty większe niż przewaga |
| A. Trzymanie miesiąc | portfel 10 spółek zmieniany co miesiąc | ✅ pobija trzymanie na 6 latach historii |
| Sieć neuronowa | własna, od zera - sama „odkryła” momentum | ✅ najlepszy z uczących się modeli |
| Sejf | jednorazowy test na ostatnim roku (XI 2025 – IX 2026) | ✅ A1, A2, A3 **zdały** (+46–54% wobec +20% trzymania) |
| 6. Gra na papierze | typy co miesiąc, rozliczenie po 20 sesjach | ⏳ trwa od 3 XI 2026 |

**Strategie, które przeszły sejf** (każda: 10 płynnych spółek z GPW, zmiana składu co 20 sesji):
- **A1** – spółki najbliżej swojego rocznego szczytu (najspokojniejsza),
- **A2** – spółki, które najbardziej urosły przez 6 miesięcy (najwyższy zysk, głębsze spadki),
- **A3** – sieć neuronowa.

Zastrzeżenia: rok w sejfie był dobry dla trendów; w złym roku (jak 2022) takie strategie potrafią
stracić 30–50%. Dlatego najpierw gra na papierze, bez pieniędzy.

### Polecenia laboratorium (z `backend`, z włączonym `.venv`)

| Polecenie | Co robi |
|---|---|
| `python -m lab.paper run` | **gra na papierze**: pobiera dane, rozlicza, co 20 sesji daje nowe typy (najważniejsze na co dzień) |
| `python -m lab.paper status` | szybki podgląd wyników gry |
| `python -m lab.data_store update` / `status` | pobranie / stan notowań |
| `python -m lab.features build` | przebudowa tabeli cech (po aktualizacji kodu) |
| `python -m lab.swing run` | test strategii tygodniowych i miesięcznych (`--szczegoly` = tabele) |
| `python -m lab.models run`, `python -m lab.backtest run` / `cfd` | handel jednodniowy (wynik: nie opłaca się) |
| `python -m lab.vault run` | sejf - już otwarty i zapieczętowany (`data/lab/vault.json`) |
| `python -m pytest lab\tests -q` | testy laboratorium |

Każdy raport zaczyna się od **werdyktu w prostych słowach**: czy lepsze od zwykłego trzymania, ile
zostałoby z 10 000 zł i czy wynik nie jest przypadkiem.

> Dziennik gry na papierze leży w `backend/data/lab/paper_journal.json` (poza Gitem). Uruchamiaj grę
> **zawsze na tym samym komputerze**. Najbliższe rozliczenie: około 2 grudnia 2026 (a jeśli przegapisz,
> program sam nadrobi zaległe miesiące).

---

## 5. Silnik C++ (opcjonalny)

`quant_engine/` to szybkie obliczenia dla laboratorium w C++20. **Bez niego wszystko działa**
(`lab/engine.py` liczy wtedy w Pythonie, z tymi samymi wynikami).

Budowa (z głównego folderu, z `.venv`):

```powershell
# kompilator Microsoftu (Build Tools for Visual Studio 2022):
pip install .\quant_engine

# albo g++ z MSYS2:
$env:CMAKE_GENERATOR = "Ninja"; $env:CC = "gcc"; $env:CXX = "g++"
pip install --force-reinstall --no-deps --no-cache-dir .\quant_engine

cd backend
python -m lab.engine        # powinno wypisać: silnik: C++ quantpulse 0.1.0
```

Windows 11 z włączoną **Inteligentną kontrolą aplikacji** blokuje samodzielnie skompilowane biblioteki
(i bywa, że świeżo zainstalowane pandas/scikit-learn). Na komputerze do programowania zwykle się ją wyłącza.
Od kwietnia 2026 da się ją potem włączyć z powrotem bez reinstalacji systemu.

---

## 6. Twoje dane i bezpieczeństwo

Wszystkie Twoje dane leżą w `backend\data\` i **celowo nie trafiają na GitHuba** (`.gitignore`):

| Plik / folder | Co zawiera |
|---|---|
| `portfolio.json` | pozycje w portfelu |
| `sales.json` | historia sprzedaży (PIT-38) |
| `portfolio_history.json` | punkty wykresu wartości portfela |
| `watchlist.json`, `alerts.json` | obserwowane spółki i alerty |
| `advice_journal.json` | Dziennik porad |
| `broker_profile.json`, `automation_*.json`, `ai_models_state.json` | ustawienia |
| `backups/` | kopie zapasowe (także automatyczne, np. przed wyczyszczeniem portfela) |
| `cache/` | rzeczy do odtworzenia (Radar, wyszukiwarka) - można skasować |
| `radar_archive/` | archiwum sygnałów Radaru dla laboratorium (zbiera, gdy backend działa) |
| `market/`, `lab/` | notowania i wyniki laboratorium |

**Zasady:**
- `backend\.env` (klucze) i `backend\data\` nigdy nie idą na GitHuba. Repozytorium jest prywatne.
- Gdyby klucz API kiedyś trafił do repozytorium, trzeba go **wygenerować na nowo** (samo usunięcie nie wystarczy).
- Klucz SSH do GitHuba: na GitHuba wysyła się tylko plik `.pub`, prywatnego klucza nie pokazuje się nikomu.
- **Przenosisz się na inny komputer?** `git pull` przyniesie kod, ale nie dane. Skopiuj ręcznie `backend\data\`
  i `backend\.env` (np. pendrivem) i zbuduj świeże `.venv` (rozdział 3).

---

## 7. Codzienne polecenia - ściąga

```powershell
# start aplikacji
cd E:\gielda\backend;  ..\.venv\Scripts\Activate.ps1;  uvicorn main:app --reload --port 8000
cd E:\gielda\frontend; npm run dev

# gra na papierze (raz na jakiś czas, wieczorem po sesji)
cd E:\gielda\backend;  python -m lab.paper run

# zapisanie zmian na GitHubie
cd E:\gielda
git add .
git commit -m "Opis zmian"
git push

# pobranie zmian na drugim komputerze
git pull
```

---

## 8. Gdy coś nie działa

| Objaw | Przyczyna i rada |
|---|---|
| `No module named ...` | nie jest włączone `.venv` (brak `(.venv)` na początku linii) albo brakuje biblioteki → `pip install -r backend\requirements.txt` |
| `DLL load failed ... Zasady kontroli aplikacji zablokowały ten plik` | Inteligentna kontrola aplikacji Windows - patrz rozdział 5 |
| Vite: `Failed to resolve import "./Cos"` / „file exist?” | brakuje pliku w `frontend\src` (np. źle rozpakowany zip) |
| Backend przy starcie: błąd odczytu JSON | pusty plik w `backend\data` (zwykle przez OneDrive) - przywróć z `backups\` albo usuń pusty plik |
| `git push`: `Internal Server Error` | chwilowa awaria GitHuba - spróbuj ponownie później (githubstatus.com) |
| `git push`: odmowa dostępu | sprawdź klucz SSH: `ssh -T git@github.com-prywatne` |
| Laboratorium: „Tabela cech jest ze starszej wersji” | `python -m lab.features build` |
| Komunikaty o `^WIG20` / `^WIG` | niegroźne - Yahoo nie ma historii tych indeksów, laboratorium liczy rynek ze średniej spółek |