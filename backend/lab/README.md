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

## Jak czytać wyniki (najpierw to)

Każdy raport modeli i backtestu zaczyna się od **werdyktu w prostych słowach**:
1. czy to lepsze niż zwykłe kupienie akcji i trzymanie ich,
2. ile zostałoby z 10 000 zł,
3. czy wynikowi można ufać (czy to nie przypadek),

a na końcu jest jednozdaniowy **wniosek**. Tabele z liczbami pokazują się tylko po dopisaniu `--szczegoly`,
np. `python -m lab.backtest run --szczegoly`.

**Laboratorium nie ma żadnego połączenia z kontem w XTB.** Wszystko to symulacje na danych z przeszłości.
Prawdziwa transakcja dzieje się tylko wtedy, gdy sam ją złożysz w aplikacji XTB.

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

## Modele (etap 4)

```powershell
python -m lab.models run            # GPW, obie wersje handlu, wszystkie modele (1-3 minuty)
python -m lab.models run --quick    # przeuczanie co kwartał zamiast co miesiąc
python -m lab.models run --label oc # tylko handel w ciągu dnia
```

- **Na czym:** tylko GPW, płynniejsza połowa spółek danego dnia, ostatni rok (sejf) pominięty.
  W wersji `oc` (od otwarcia do zamknięcia) tylko dni z prawdziwym otwarciem w Yahoo.
- **Zawodnicy:** losowy wybór, proste reguły z jedną cechą, regresja logistyczna, LightGBM.
- **Walk-forward:** model uczy się na danych do końca miesiąca M (z jednodniową przerwą) i typuje miesiąc M+1.
  Potem okno przesuwa się o miesiąc. Każda prognoza powstaje wyłącznie z przeszłości.
- **Wynik:** codziennie 5 spółek z góry rankingu. Liczymy ich średni zwrot ponad średnią spółek (w pb, 1 pb = 0.01%),
  trafność, stabilność rok po roku i porównanie handlu w ciągu dnia z handlem z nocą. Wszystko **przed kosztami**.
- **Licznik prób** (`data/lab/trials.json`): każda nowa konfiguracja to próba. Im więcej prób, tym wyższe
  t potrafi wyjść przypadkiem, więc raport podaje, czy najlepszy wynik jest ponad ten poziom.
- Prognozy trafiają do `data/lab/predictions_oc.parquet` i `predictions_cc.parquet`, a skorzysta z nich backtest w etapie 5.

## Backtest z kosztami (etap 5)

```powershell
python -m lab.backtest run          # około minuty; najpierw musi być python -m lab.models run
```

- Odtwarza handel dzień po dniu na prognozach „na ślepo” z etapu 4: wieczorem ocena, rano kupno
  po cenie otwarcia, wieczorem sprzedaż po cenie zamknięcia.
- **Koszty XTB:** 0% prowizji do 100 000 EUR obrotu w miesiącu, potem 0.2%, ale minimum 10 EUR za transakcję.
  Do tego poślizg: 0 pb przy zleceniach na fixing, 10 pb na stronę w trakcie sesji. Kupujemy całe akcje.
- **Bez podatku Belki:** na IKE/IKZE go nie ma, na zwykłym koncie odejmij 19% od rocznego zysku.
- **Scenariusze:** wszyscy zawodnicy, różny kapitał (5, 20, 50 tys. zł), mniej spółek albo próg „tylko pewne typy”
  (liczony wyłącznie z ocen z przeszłości), rok po roku na tle średniej spółek GPW.
- **Gra na różnicy przez CFD** (`python -m lab.backtest cfd`): rano kupno najlepiej ocenionych i krótka
  sprzedaż najgorzej ocenionych spółek, wieczorem zamknięcie obu stron. Koszty CFD na polskie akcje w XTB:
  prowizja 0%, spread 0.30% (tabela opłat XTB z 14.03.2026). Raport pokazuje też próg opłacalności,
  czyli przy jakim spreadzie strategia wychodzi na zero.
- Każda konfiguracja trafia do licznika prób. Najlepsza wybrana spośród wielu jest trochę zawyżona,
  więc ostateczny sprawdzian to sejf.

## Trzymanie przez tydzień albo miesiąc (opcja A)

```powershell
python -m lab.features build      # raz, po aktualizacji - dochodzą etykiety na 5 i 20 sesji
python -m lab.swing run           # werdykt dla tygodnia i miesiąca (kilka minut)
python -m lab.swing run --szczegoly
```

- Co tydzień (5 sesji) albo co miesiąc (20 sesji) model wybiera 10 płynnych spółek z GPW. Kupujesz je po równo
  i trzymasz do następnej zmiany składu. Spółki, które zostają na liście, zostają też w portfelu,
  więc płacisz tylko za zmiany.
- Kupno i sprzedaż na fixingu zamknięcia (ceny zamknięcia w Yahoo są wiarygodne, otwarcia nie zawsze).
  Poślizg 0.10% na stronę, prowizja XTB, całe akcje, bez podatku Belki (IKE/IKZE).
- Nowe cechy długoterminowe: zwrot z 3 i 6 miesięcy oraz momentum 12-1 (zwrot z roku bez ostatniego miesiąca).
- Punkt odniesienia: wszystkie spółki z listy kupione po równo i trzymane.

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
4. ✅ Modele i walk-forward
5. ✅ Backtest z kosztami XTB
6. Paper trading
7. Zakładka w aplikacji
