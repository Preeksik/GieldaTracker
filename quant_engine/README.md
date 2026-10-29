# quant_engine: silnik C++20 QuantPulse

Szybkie obliczenia dla laboratorium (`backend/lab`). Kod jest w C++, a z Pythona używasz go jak zwykłego
pakietu: `import quantpulse`. Bez silnika laboratorium też działa, bo liczy wtedy w numpy/pandas
(`backend/lab/engine.py`).

## Co już jest

| Plik | Do czego |
|---|---|
| `include/quantpulse/rolling.hpp` | `RollingWindow`: średnia, odchylenie i z-score w przesuwanym oknie, liczone w locie (Welford) z kontrolą błędu zaokrągleń |
| `src/bindings.cpp` | most do Pythona (pybind11) |
| `tests/python/test_parity.py` | zgodność z dokładnym liczeniem w numpy do 1e-9 |
| `tests/cpp/test_rolling.cpp` | testy C++ (GoogleTest) |

## Instalacja na Windowsie (raz)

1. **Kompilator C++.** Pobierz *Build Tools for Visual Studio 2022* ze strony Microsoftu
   (visualstudio.microsoft.com → Downloads → Tools for Visual Studio → Build Tools).
   W instalatorze zaznacz **„Programowanie aplikacji klasycznych w języku C++”**
   („Desktop development with C++”) i zainstaluj. Zajmie to kilka GB i około 10–20 minut.
2. **Zbudowanie i instalacja silnika** z głównego folderu repozytorium, z włączonym `.venv`:
   ```powershell
   cd C:\Users\adria\OneDrive\Pulpit\GieldaTracker
   pip install .\quant_engine
   ```
   CMake i pybind11 pip pobierze sam. Kompilacja trwa około minuty.
3. **Sprawdzenie:**
   ```powershell
   cd backend
   python -m lab.engine                 # powinno wypisać: silnik: C++ quantpulse 0.1.0
   pip install pytest
   python -m pytest ..\quant_engine\tests\python -q
   python -m pytest lab\tests -q
   ```

Po każdej zmianie w kodzie C++ powtórz `pip install .\quant_engine`.

## Testy C++ (opcjonalnie)

```powershell
cd quant_engine
cmake -S . -B build -DQP_BUILD_PYTHON=OFF -DQP_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build -C Release
```

Potrzebny jest do tego program `cmake` w terminalu (`pip install cmake`). GoogleTest pobierze się sam z GitHuba.

## Dlaczego nie od razu szybciej niż pandas

Jedna funkcja, np. `rolling_std`, jest tak samo szybka jak w pandas, bo pandas też liczy ją w skompilowanym kodzie.
Przewaga silnika przyjdzie w następnych krokach:

- **`FeatureEngine`**: wszystkie cechy dla spółki w jednym przejściu po danych, zamiast osobnego przejścia na każdą cechę.
- **`ThreadPool`**: wiele spółek naraz, na wszystkich rdzeniach.
- **Backtester**: tysiące symulacji z różnymi ustawieniami.

Dokładność już teraz jest lepsza: pandas na długich seriach odpływa o około 5e-9, a nasz silnik trzyma około 1e-10.
