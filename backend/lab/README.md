# QuantPulse — laboratorium

Osobna część HossaLab do badania, czy da się przewidywać krótkoterminowe ruchy cen.
Na razie **nie dotyka aplikacji**: uruchamiasz ją poleceniami w terminalu.

## Co już jest

| Plik | Do czego |
|---|---|
| `universe.py` | lista instrumentów: ~90 spółek z GPW, 100 z USA, indeksy (WIG20, WIG, S&P 500, Nasdaq 100, VIX), kursy EUR/PLN i USD/PLN |
| `data_store.py` | pobiera notowania dzienne z Yahoo (10 lat), zapisuje je, przy kolejnym uruchomieniu dociąga tylko nowe dni i sprawdza jakość danych |
| `features.py` | z notowań buduje tabelę do nauki: 17 cech na spółkę i dzień oraz etykietę (co spółka zrobi jutro ponad rynek) |
| `engine.py` | liczy statystyki silnikiem C++ (`quant_engine`), a jeśli go nie ma, w Pythonie |
| `tests/` | testy bez internetu (Yahoo jest zastąpione sztucznymi danymi) |

Dane trafiają do `backend/data/market/`, po jednym pliku na instrument. Folder `data/` jest w `.gitignore`,
więc nic z tego nie poleci na GitHuba.

## Instalacja (raz)

```powershell
cd C:\Users\adria\OneDrive\Pulpit\GieldaTracker\backend
pip install -r lab\requirements-lab.txt
```

Jeśli `pyarrow` nie zainstaluje się na Pythonie 3.14, nic nie szkodzi: dane zapiszą się jako `.csv.gz`
(wolniej, ale działa).

## Uruchamianie (zawsze z folderu `backend`)

```powershell
python -m lab.data_store update     # pierwszy raz: kilka minut; potem kilkanaście sekund
python -m lab.data_store status     # raport o stanie bazy
python -m pytest lab\tests -q       # testy
```

Dodatkowe opcje:

- `python -m lab.data_store update --full`: pobiera wszystko od nowa.
- `python -m lab.data_store update --only PKO.WA,AAPL`: pobiera tylko wybrane instrumenty.

## Cechy i etykiety

```powershell
python -m lab.features build         # buduje backend\data\lab\dataset.parquet + raport (kilka sekund)
python -m lab.features report        # sam raport
python -m lab.features show PKO.WA   # cechy z ostatnich dni jednej spółki
```

- **Wiersz tabeli** to spółka × dzień sesji *t*. Cechy liczą się tylko z danych znanych po zamknięciu dnia *t*.
- **Etykieta `y_oc_ex`** to zwrot spółki **jutro** od otwarcia do zamknięcia, minus średnia wszystkich spółek
  tego rynku tego dnia. Model ma wskazać spółki, które jutro wypadną lepiej od reszty.
- **Sejf**: ostatnie 12 miesięcy. Raport ich nie ogląda. Posłużą do jednego, końcowego, uczciwego sprawdzianu.
- **IC** w raporcie to korelacja rangowa cechy z jutrzejszym wynikiem, liczona w obrębie każdego dnia i uśredniona.
  W finansach 0.01–0.03 to już coś, a |t| < 2 to szum. Pojedyncza cecha z wysokim |t| to dopiero trop, nie strategia.
- Dla spółek z GPW VIX jest brany z poprzedniego dnia, bo o 17:30 sesja w USA jeszcze trwa.
  Test `test_no_lookahead` pilnuje, żeby żadna cecha nie podglądała przyszłości.

## Jak czytać `status`

- **Bez danych w Yahoo**: symbolu nie ma w Yahoo (spółka zniknęła z giełdy albo zmieniła symbol).
  Jeśli powtórzy się przy kolejnym pobraniu, usuń go z `universe.py`.
- **Nieaktualne**: instrument nie ma ostatniej sesji, którą ma jego indeks. Zwykle brak obrotu albo zawieszenie notowań.
- **Podejrzane dni**: dni z błędami w danych, na przykład brak ceny, cena spoza zakresu dnia albo szpilka, która następnego dnia wraca.
  Nie są usuwane, tylko oznaczone, a model ich nie użyje.
- **Brakujące sesje**: dni, w które indeks był notowany, a spółka nie. Kilka dni to norma, kilkadziesiąt to sygnał ostrzegawczy.

## Ważne zasady

- `close` to cena skorygowana tylko o splity, a `adj_close` także o dywidendy. Stopy zwrotu liczymy
  z `adj_close`, a koszty i wielkość pozycji z `close`.
- Jeśli Yahoo przeliczy historię (split albo dywidenda), instrument jest pobierany od nowa w całości, żeby
  stare i nowe ceny do siebie pasowały.
- Lista spółek to spółki notowane **dziś**. Te, które przez 10 lat zbankrutowały, nie mają danych, więc
  testy historyczne będą nieco zbyt optymistyczne (tzw. survivorship bias).

## Co dalej

1. ✅ Dane
2. ✅ Silnik C++20 (`quant_engine/`): pierwszy element (`RollingWindow`). Na Windowsie z włączoną
   Inteligentną kontrolą aplikacji jest zablokowany, więc laboratorium liczy w Pythonie z tym samym wynikiem
3. ✅ Cechy i etykiety
4. Modele i walk-forward
5. Backtest z kosztami XTB
6. Paper trading
7. Zakładka w aplikacji
