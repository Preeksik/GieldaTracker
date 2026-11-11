import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dividend_payments as D  # noqa: E402

HTML = """<table><thead><tr><th>Profil</th><th>Dywidenda za rok</th><th>Data WZA</th>
<th>Ostatnie notowanie z prawem do dywidendy</th><th>Dzień wypłaty</th><th>Dywidenda na akcję</th>
<th>Stopa dywidendy*</th><th>Status</th></tr></thead><tbody>
<tr><td>KTY (KETY)</td><td>2025</td><td>18 cze 26</td><td>17 sie 26</td><td>03 wrz 26</td><td>16,32 PLN</td><td></td><td>wypłacona</td></tr>
<tr><td>KTY (KETY)</td><td>2025</td><td>18 cze 26</td><td>17 sie 26</td><td>04 lis 26</td><td>32,64 PLN</td><td></td><td>wypłacona</td></tr>
<tr><td>VOX (VOXEL)</td><td>2025</td><td>30 cze 26</td><td>28 wrz 26</td><td>04 gru 26</td><td>2,50 PLN</td><td></td><td>uchwalona</td></tr>
<tr><td>PZU</td><td>2025</td><td>18 cze 26</td><td>15 wrz 26</td><td>08 paź 26</td><td>4,80 PLN</td><td></td><td>wypłacona</td></tr>
<tr><td>ASB (ASBIS)</td><td>2026</td><td>04 lis 26</td><td>12 lis 26</td><td>26 lis 26</td><td>1,37 PLN</td><td></td><td>uchwalona</td></tr>
<tr><td>MUR (MURAPOL)</td><td>2026</td><td>27 paź 26</td><td>20 lis 26</td><td>30 lis 26</td><td>2,94 PLN</td><td></td><td>uchwalona</td></tr>
<tr><td>IMS</td><td>2026</td><td>20 paź 26</td><td>11 lis 26</td><td>03 gru 26</td><td>0,18 PLN</td><td></td><td>uchwalona</td></tr>
<tr><td>WWL (WAWEL)</td><td>2026</td><td>bd.</td><td>bd.</td><td>bd.</td><td>35,00 PLN</td><td></td><td></td></tr>
</tbody></table>"""
TODAY = date(2026, 11, 11)


def test_parse_polish_dates_and_rows():
    assert D.parse_pl_date("01 paź 26") == "2026-10-01" and D.parse_pl_date("bd.") is None
    rows = D.parse_table(HTML)
    assert [r["symbol"] for r in rows] == ["KTY", "KTY", "VOX", "PZU", "ASB", "MUR", "IMS", "WWL"]
    vox = rows[2]
    assert vox["payment_date"] == "2026-12-04" and vox["last_with_right"] == "2026-09-28"
    assert vox["status"] == "uchwalona" and vox["amount"] == "2,50 PLN"
    assert rows[-1]["payment_date"] is None and rows[-1]["status"] is None
    assert D.parse_table("<p>nowy wygląd strony</p>") == []


def test_enrich_upcoming_paid_and_yahoo():
    pos = [{"ticker": "VOX.WA"}, {"ticker": "KTY.WA", "realized_dividends": [{"date": "2026-08-18"}]},
           {"ticker": "AAPL", "yahoo_payment_date": "2026-11-16"}, {"ticker": "VWCE.DE"},
           {"ticker": "PZU.WA", "realized_dividends": [{"date": "2026-09-16"}, {"date": "2025-09-10"}]}]
    D.enrich(pos, D.parse_table(HTML), today=TODAY)
    vox, kty, aapl, etf, pzu = pos
    assert vox["next_payment"]["date"] == "2026-12-04" and not vox["next_payment"]["paid"]
    assert vox["next_payment"]["status"] == "uchwalona" and vox["next_payment"]["source"] == "BiznesRadar"
    assert kty["next_payment"]["date"] == "2026-11-04" and kty["next_payment"]["paid"]     # druga rata, już wypłacona
    assert len(kty["payments"]) == 2
    assert kty["realized_dividends"][0]["payment_date"] == "2026-09-03, 2026-11-04"         # dwie raty
    assert aapl["next_payment"] == {"date": "2026-11-16", "status": "zapowiedziana", "source": "Yahoo",
                                    "amount": None, "last_with_right": None, "paid": False, "more": 0}
    assert etf["next_payment"] is None and etf["payments"] == []
    assert pzu["realized_dividends"][0]["payment_date"] == "2026-10-08"
    assert "payment_date" not in pzu["realized_dividends"][1]                                # stara - poza tabelą


def test_cache_fresh_stale_and_failure(tmp_path):
    cache = str(tmp_path / "c.json")
    calls = []

    def fetch():
        calls.append(1)
        return HTML
    now = datetime(2026, 11, 11, 10)
    rows, src = D.load_rows(fetch, cache, now)
    assert len(rows) == 8 and src["fetched_at"] == "2026-11-11T10:00:00"
    D.load_rows(fetch, cache, datetime(2026, 11, 11, 20))                  # 10 h później - bez pobierania
    assert len(calls) == 1

    def broken():
        raise OSError("brak internetu")
    rows, src = D.load_rows(broken, cache, datetime(2026, 11, 12, 12))     # po 12 h, strona nie działa
    assert len(rows) == 8 and src["stale"] is True
    rows, src = D.load_rows(broken, str(tmp_path / "none.json"), now)
    assert rows == [] and "brak internetu" in src["error"]
    with open(cache, encoding="utf-8") as f:
        assert json.load(f)["rows"][0]["symbol"] == "KTY"


def test_past_ex_dates_are_not_shown_as_next():
    pos = [{"ticker": "KTY.WA", "next_ex_date": "2026-08-18"},          # Yahoo: sierpień - już minęło
           {"ticker": "VGO.WA", "next_ex_date": "2017-05-30"},          # Yahoo: 2017
           {"ticker": "ASB.WA", "next_ex_date": None},                  # Yahoo nic, BiznesRadar zna
           {"ticker": "MUR.WA", "next_ex_date": "2025-11-20"},          # ostatni dzień z prawem w piątek
           {"ticker": "IMS.WA"},                                        # ostatni dzień z prawem = dziś
           {"ticker": "AAPL", "next_ex_date": "2026-11-12"}]            # USA: przyszła data z Yahoo zostaje
    D.enrich(pos, D.parse_table(HTML), today=TODAY)
    kty, vgo, asb, mur, ims, aapl = pos
    assert kty["next_ex_date"] is None and kty["last_ex_date"] == "2026-08-18"
    assert vgo["next_ex_date"] is None and vgo["last_ex_date"] == "2017-05-30"
    assert asb["next_ex_date"] == "2026-11-13" and asb["last_with_right"] == "2026-11-12"
    assert asb["next_ex_source"] == "BiznesRadar"
    assert mur["next_ex_date"] == "2026-11-23" and mur["last_ex_date"] == "2025-11-20"  # piątek -> poniedziałek
    assert ims["next_ex_date"] == "2026-11-12"                                         # dziś jeszcze można kupić
    assert aapl["next_ex_date"] == "2026-11-12" and aapl["next_ex_source"] == "Yahoo"


def test_net_amounts_depend_on_account():
    entries = [{"ticker": "AMB.WA", "quantity": 111, "account": "zwykle"},
               {"ticker": "AMB.WA", "quantity": 27, "account": "zwykle"},
               {"ticker": "PZU.WA", "quantity": 42, "account": "ike"},
               {"ticker": "MIX.WA", "quantity": 30, "account": "zwykle"},
               {"ticker": "MIX.WA", "quantity": 10, "account": "ikze"},
               {"ticker": "AAPL", "quantity": 4, "account": "zwykle"}]
    pos = [{"ticker": "AMB.WA", "realized_dividends": [{"total_received": 151.8}], "realized_total_native": 151.8,
            "realized_total_pln": 151.8, "next_amount_estimate_total": 151.8, "annual_estimate_total_pln": 151.8},
           {"ticker": "PZU.WA", "realized_dividends": [{"total_received": 201.6}], "realized_total_native": 201.6,
            "realized_total_pln": 201.6, "next_amount_estimate_total": None, "annual_estimate_total_pln": None},
           {"ticker": "MIX.WA", "realized_total_native": 100.0, "realized_total_pln": 100.0},
           {"ticker": "AAPL", "realized_total_native": 4.0, "realized_total_pln": 15.0}]
    summary = {}
    D.add_net(pos, entries, summary)
    amb, pzu, mix, aapl = pos
    assert amb["realized_dividends"][0]["net_received"] == 122.96                  # 151,80 - 19%
    assert amb["realized_total_net_pln"] == 122.96 and amb["tax_note"] == "po 19% podatku"
    assert amb["next_amount_estimate_net_total"] == 122.96
    assert pzu["realized_total_net_pln"] == 201.6 and "IKE" in pzu["tax_note"]     # IKE - bez podatku
    assert mix["realized_total_net_pln"] == 85.75                                  # 30 z 40 akcji opodatkowane
    assert "30 z 40" in mix["tax_note"]
    assert aapl["net_is_estimate"] and aapl["realized_total_net_native"] == 3.24
    assert summary["total_realized_last_12mo_net_pln"] == round(122.96 + 201.6 + 85.75 + 12.15, 2)


def test_gpw_holidays():
    import gpw_calendar as G
    assert G.easter(2026) == date(2026, 4, 5) and G.easter(2027) == date(2027, 3, 28)
    h = G.holidays(2026)
    assert {date(2026, 4, 3), date(2026, 4, 6), date(2026, 6, 4), date(2026, 11, 11), date(2026, 12, 24)} <= h
    assert G.next_session("2026-11-10") == "2026-11-12"          # 11 listopada - giełda zamknięta
    assert G.prev_session("2026-11-12") == "2026-11-10"
    assert G.next_session("2026-12-23") == "2026-12-28"          # Wigilia, święta, weekend
    assert G.next_session("2026-04-02") == "2026-04-07"          # Wielki Piątek + Poniedziałek Wielkanocny
    assert G.next_session("2026-11-12") == "2026-11-13"          # zwykły dzień
    # dzień odcięcia z BiznesRadar też omija święto
    pos = [{"ticker": "IMS.WA"}]
    D.enrich(pos, D.parse_table(HTML.replace("<td>11 lis 26</td><td>03 gru 26</td>", "<td>10 lis 26</td><td>03 gru 26</td>")),
             today=date(2026, 11, 9))
    assert pos[0]["next_ex_date"] == "2026-11-12"


def test_shares_held_on_ex_date():
    entries = [{"ticker": "AMB.WA", "quantity": 111, "account": "zwykle", "buy_date": "2026-04-23"},
               {"ticker": "AMB.WA", "quantity": 27, "account": "ike", "buy_date": "2026-10-30"},     # po odcięciu
               {"ticker": "AMB.WA", "quantity": 5, "account": "zwykle", "buy_date": "2026-10-27"}]    # W dniu odcięcia
    sales = [{"ticker": "AMB.WA", "quantity": 10, "account": "ike", "buy_date": "2026-01-10", "sell_date": "2026-10-27"},
             {"ticker": "AMB.WA", "quantity": 7, "account": "zwykle", "buy_date": "2026-01-10", "sell_date": "2026-10-26"},
             {"ticker": "PZU.WA", "quantity": 3, "account": "zwykle", "buy_date": "2026-01-10", "sell_date": "2026-12-01"}]
    held = D.held_at("AMB.WA", "2026-10-27", entries, sales)
    # 111 (kupione wcześniej) + 10 sprzedane w dniu odcięcia (prawo zostaje); bez 27 i 5 (za późno) i bez 7 (sprzedane przed)
    assert held == {"zwykle": 111.0, "ike": 10.0}
    assert D.held_at("AMB.WA", "2026-01-05", entries, sales) == {}


def test_net_uses_accounts_from_ex_date():
    entries = [{"ticker": "AMB.WA", "quantity": 100, "account": "ike"}]       # dziś wszystko na IKE
    pos = [{"ticker": "AMB.WA", "realized_total_native": 110.0, "realized_total_pln": 110.0,
            "realized_dividends": [{"total_received": 110.0, "accounts": {"zwykle": 100.0}}]}]  # wtedy na zwykłym
    D.add_net(pos, entries, {})
    assert pos[0]["realized_dividends"][0]["net_received"] == 89.1           # 19% - bo wtedy było konto zwykłe
    assert pos[0]["realized_total_net_pln"] == 89.1
