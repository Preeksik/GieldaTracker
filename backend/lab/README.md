# QuantPulse — laboratorium

Osobna część HossaLab do badania, czy da się przewidywać krótkoterminowe ruchy cen.
Na razie **nie dotyka aplikacji**: uruchamiasz ją poleceniami w terminalu.

## Co już jest (etap 1: dane)

| Plik | Do czego |
|---|---|
| `universe.py` | lista instrumentów: ~90 spółek z GPW, 100 z USA, indeksy (WIG20, WIG, S&P 500, Nasdaq 100, VIX), kursy EUR/PLN i USD/PLN |
| `data_store.py` | pobiera notowania dzienne z Yahoo (10 lat), zapisuje je, przy kolejnym uruchomieniu dociąga tylko nowe dni i sprawdza jakość danych |
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
py -m lab.data_store update     # pierwszy raz: kilka minut; potem kilkanaście sekund
py -m lab.data_store status     # raport o stanie bazy
py -m pytest lab\tests -q       # testy
```

Dodatkowe opcje:

- `py -m lab.data_store update --full`: pobiera wszystko od nowa.
- `py -m lab.data_store update --only PKO.WA,AAPL`: pobiera tylko wybrane instrumenty.

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
2. Silnik C++20 (`quant_engine/`): szybkie liczenie cech, testy zgodności z Pythonem
3. Cechy i etykiety
4. Modele i walk-forward
5. Backtest z kosztami XTB
6. Paper trading
7. Zakładka w aplikacji
