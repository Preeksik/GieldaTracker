"""
HossaLab - wyczyszczenie całego portfela jednym przyciskiem (z dwustopniowym potwierdzeniem).

Endpointy:
    GET  /api/portfolio/clear/preview   ile czego jest (pozycje, sprzedaże, punkty wykresu)
    POST /api/portfolio/clear           {"confirm": "USUŃ", "include_sales": true, "include_history": true}

BEZPIECZEŃSTWO:
  - bez dokładnego słowa "USUŃ" w żądaniu nic nie jest usuwane (zabezpieczenie przed przypadkowym
    wywołaniem, np. podwójnym kliknięciem albo starym skryptem),
  - PRZED usunięciem zawsze powstaje automatyczna kopia zapasowa (folder data/backups) -
    w razie pomyłki całość da się przywrócić w panelu "Kopia zapasowa".
  - Nie rusza: watchlisty, alertów, dziennika porad, ustawień ani danych laboratorium.
"""

import logging

from pydantic import BaseModel

logger = logging.getLogger("gpw-api")

CONFIRM_WORD = "USUŃ"


class ClearRequest(BaseModel):
    confirm: str = ""
    include_sales: bool = True
    include_history: bool = True


def setup_portfolio_clear(app, load_portfolio, save_portfolio, load_sales, save_sales,
                          load_history, save_history, snapshot_fn):
    """Funkcje przekazujemy z main.py (najlepiej jako lambdy - zawsze aktualne wersje)."""
    from fastapi import HTTPException

    @app.get("/api/portfolio/clear/preview")
    def portfolio_clear_preview():
        return {"positions": len(load_portfolio() or []), "sales": len(load_sales() or []),
                "history_points": len(load_history() or []), "confirm_word": CONFIRM_WORD}

    @app.post("/api/portfolio/clear")
    def portfolio_clear(req: ClearRequest):
        if (req.confirm or "").strip().upper() != CONFIRM_WORD:
            raise HTTPException(status_code=400,
                                detail=f'Żeby usunąć portfel, wpisz dokładnie słowo {CONFIRM_WORD}.')
        before = {"positions": len(load_portfolio() or []), "sales": len(load_sales() or []),
                  "history_points": len(load_history() or [])}
        try:
            snapshot = snapshot_fn()
        except Exception:
            logger.exception("Nie udało się zrobić kopii zapasowej przed wyczyszczeniem portfela")
            raise HTTPException(status_code=500,
                                detail="Nie udało się zrobić kopii zapasowej - dla bezpieczeństwa niczego nie usunięto.")
        save_portfolio([])
        removed = {"positions": before["positions"], "sales": 0, "history_points": 0}
        if req.include_sales:
            save_sales([])
            removed["sales"] = before["sales"]
        if req.include_history:
            save_history([])
            removed["history_points"] = before["history_points"]
        logger.info("Wyczyszczono portfel: %s (kopia: %s)", removed, snapshot)
        return {"removed": removed, "safety_snapshot": snapshot}
