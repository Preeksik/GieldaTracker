"""
QuantPulse - zestaw instrumentów, które laboratorium pobiera i bada.

Składy indeksów są z wiedzy na połowę 2026 r. i mogą być nieaktualne: spółki wchodzą
do indeksów i z nich wypadają, niektóre znikają z giełdy. Nie szkodzi - pobieranie
oznacza symbole, których Yahoo nie zna, a raport `status` je wypisuje do usunięcia.

Uwaga o "survivorship bias": lista zawiera spółki notowane DZIŚ. Spółki, które przez
10 lat zbankrutowały albo zeszły z giełdy, nie mają danych w Yahoo, więc wyniki
historycznych testów będą nieco zawyżone. Raporty laboratorium mówią o tym wprost.
"""

# Usunięte 2026-10-30 (Yahoo dwa razy nie zwróciło danych): SPL.WA, CCC.WA, CMR.WA, GEA.WA,
# TIM.WA, SEN.WA, BK. CMR i TIM zeszły z giełdy; CCC, SPL i BK są notowane - do sprawdzenia symbol.

# GPW - największe i najpłynniejsze (WIG20, mWIG40, część sWIG80), symbole Yahoo (.WA)
GPW = [
    # duże spółki
    "PKO.WA", "PKN.WA", "PZU.WA", "PEO.WA", "KGH.WA", "LPP.WA", "DNP.WA", "ALE.WA", "CDR.WA",
    "MBK.WA", "ALR.WA", "BDX.WA", "KRU.WA", "OPL.WA", "PGE.WA", "CPS.WA", "JSW.WA", "KTY.WA",
    "PCO.WA", "ZAB.WA", "TPE.WA", "ACP.WA", "ENA.WA", "BHW.WA", "ING.WA", "MIL.WA", "BNP.WA", "XTB.WA",
    # średnie spółki
    "11B.WA", "1AT.WA", "ABE.WA", "AMC.WA", "APR.WA", "ASB.WA", "ASE.WA", "ATT.WA", "BFT.WA", "BRS.WA",
    "CAR.WA", "COG.WA", "DOM.WA", "DVL.WA", "EAT.WA", "EUR.WA", "GPW.WA", "GPP.WA", "LWB.WA",
    "MRB.WA", "NEU.WA", "PEP.WA", "STP.WA", "TEN.WA", "TXT.WA", "VRG.WA", "WPL.WA", "ZEP.WA", "PLW.WA",
    "HUG.WA", "ACG.WA", "SLV.WA", "APT.WA", "ARH.WA", "BOS.WA", "KGN.WA", "MAB.WA", "RVU.WA", "SNT.WA",
    "VOX.WA", "CRJ.WA", "DAT.WA", "AMB.WA", "QNT.WA", "TOR.WA", "MBR.WA", "ECH.WA", "STX.WA", "INK.WA",
    "BIO.WA", "CBF.WA", "MCI.WA", "PXM.WA", "UNT.WA", "WLT.WA", "ERB.WA",
]

# USA - ok. 100 największych spółek (zbliżone do S&P 100)
USA = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "AVGO", "JPM",
    "LLY", "V", "MA", "UNH", "XOM", "COST", "HD", "PG", "JNJ", "WMT",
    "NFLX", "ABBV", "BAC", "CRM", "ORCL", "CVX", "KO", "MRK", "AMD", "PEP",
    "ADBE", "TMO", "ACN", "LIN", "MCD", "CSCO", "ABT", "WFC", "IBM", "GE",
    "DHR", "INTU", "QCOM", "TXN", "CAT", "AMGN", "VZ", "PM", "NOW", "ISRG",
    "GS", "MS", "AXP", "BKNG", "SPGI", "T", "NEE", "PFE", "CMCSA", "DIS",
    "RTX", "UBER", "LOW", "HON", "UNP", "AMAT", "BLK", "SCHW", "C", "PLTR",
    "SBUX", "LMT", "DE", "BA", "MDT", "GILD", "BMY", "MO", "MDLZ", "CL",
    "SO", "DUK", "COP", "UPS", "FDX", "NKE", "INTC", "MU", "PYPL", "TGT",
    "GM", "F", "MMM", "GD", "USB", "COF", "MET", "AIG", "CVS",
]

# Indeksy - tło rynku dla cech i punkt odniesienia dla wyników.
# Klucz = nazwa w laboratorium, wartość = kandydaci na symbol w Yahoo (pierwszy, który działa).
INDEXES = {
    "WIG20": ["WIG20.WA", "^WIG20"],
    "WIG": ["WIG.WA", "^WIG"],
    "SP500": ["^GSPC"],
    "NDX": ["^NDX"],
    "VIX": ["^VIX"],
}

# Kursy walut - koszty i wyniki w złotych
FX = {
    "EURPLN": ["EURPLN=X"],
    "USDPLN": ["USDPLN=X"],
}

# Indeks odniesienia dla każdego rynku (do etykiety "zwrot ponad rynek")
BENCHMARK = {"PL": "WIG20", "US": "SP500"}


def market_of(ticker):
    """'PKO.WA' -> 'PL', 'AAPL' -> 'US'."""
    return "PL" if ticker.upper().endswith(".WA") else "US"


def all_stocks():
    return list(GPW) + list(USA)