import { useState, useEffect } from 'react'

const API_URL = 'http://127.0.0.1:8000'

function PortfolioNews() {
  const [tickers, setTickers] = useState([])
  const [newTicker, setNewTicker] = useState('')
  const [watchlistLoading, setWatchlistLoading] = useState(false)
  const [watchlistError, setWatchlistError] = useState('')

  const [report, setReport] = useState('')
  const [reportLoading, setReportLoading] = useState(false)
  const [reportError, setReportError] = useState('')

  const fetchWatchlist = async () => {
    setWatchlistLoading(true)
    setWatchlistError('')
    try {
      const res = await fetch(`${API_URL}/api/watchlist`)
      if (!res.ok) throw new Error('Nie udało się pobrać watchlisty.')
      const data = await res.json()
      setTickers(data.tickers)
    } catch (err) {
      setWatchlistError(err.message)
    } finally {
      setWatchlistLoading(false)
    }
  }

  useEffect(() => {
    fetchWatchlist()
  }, [])

  const addTicker = async () => {
    if (!newTicker.trim()) return
    try {
      const res = await fetch(`${API_URL}/api/watchlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: newTicker }),
      })
      if (!res.ok) throw new Error('Nie udało się dodać tickera.')
      const data = await res.json()
      setTickers(data.tickers)
      setNewTicker('')
    } catch (err) {
      setWatchlistError(err.message)
    }
  }

  const removeTicker = async (ticker) => {
    try {
      const res = await fetch(`${API_URL}/api/watchlist/${ticker}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Nie udało się usunąć tickera.')
      const data = await res.json()
      setTickers(data.tickers)
    } catch (err) {
      setWatchlistError(err.message)
    }
  }

  const fetchCatalysts = async () => {
    setReportLoading(true)
    setReportError('')
    setReport('')
    try {
      const res = await fetch(`${API_URL}/api/watchlist/catalysts`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się wygenerować radaru katalizatorów.')
      }
      const data = await res.json()
      setReport(data.report)
    } catch (err) {
      setReportError(err.message)
    } finally {
      setReportLoading(false)
    }
  }

  return (
    <div style={{ color: '#fff' }}>
      <h2 style={{ marginBottom: '10px' }}>🔥 Radar katalizatorów</h2>
      <p style={{ color: '#aaa', marginBottom: '20px', maxWidth: '700px' }}>
        Watchlista niezależna od portfela - obserwuj spółki pod kątem nadchodzących raportów
        finansowych i dużych wydarzeń, niezależnie czy je posiadasz. Pod agresywne, krótkoterminowe granie.
      </p>

      {/* Zarządzanie watchlistą */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '15px', flexWrap: 'wrap' }}>
        <input
          placeholder="Dodaj ticker (np. NVDA, TSLA, CDR.WA)"
          value={newTicker}
          onChange={(e) => setNewTicker(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && addTicker()}
          style={{
            padding: '10px',
            fontSize: '14px',
            borderRadius: '5px',
            border: '1px solid #ccc',
            color: '#fff',
            backgroundColor: '#333',
            width: '250px',
          }}
        />
        <button
          onClick={addTicker}
          style={{
            padding: '10px 20px',
            background: '#007BFF',
            color: 'white',
            border: 'none',
            borderRadius: '5px',
            cursor: 'pointer',
          }}
        >
          Dodaj do watchlisty
        </button>
      </div>

      {watchlistError && <div style={{ color: '#FF5252', marginBottom: '15px' }}>{watchlistError}</div>}

      {watchlistLoading ? (
        <div style={{ color: '#aaa', marginBottom: '20px' }}>Ładowanie watchlisty...</div>
      ) : (
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '25px' }}>
          {tickers.length === 0 && <span style={{ color: '#777' }}>Watchlista jest pusta.</span>}
          {tickers.map((t) => (
            <span
              key={t}
              style={{
                background: '#333',
                padding: '6px 10px',
                borderRadius: '15px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '13px',
              }}
            >
              {t}
              <button
                onClick={() => removeTicker(t)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: '#FF5252',
                  cursor: 'pointer',
                  fontWeight: 'bold',
                  padding: 0,
                }}
                title="Usuń z watchlisty"
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Radar katalizatorów */}
      <button
        onClick={fetchCatalysts}
        disabled={reportLoading || tickers.length === 0}
        style={{
          padding: '12px 24px',
          background: '#007BFF',
          color: 'white',
          border: 'none',
          borderRadius: '5px',
          cursor: reportLoading || tickers.length === 0 ? 'not-allowed' : 'pointer',
          fontSize: '15px',
          marginBottom: '20px',
        }}
      >
        {reportLoading ? 'Skanuję watchlistę... (może potrwać do minuty)' : '📡 Sprawdź nadchodzące wydarzenia'}
      </button>

      {reportError && (
        <div style={{ color: '#FF5252', marginBottom: '20px', background: '#3a1f1f', padding: '12px', borderRadius: '6px' }}>
          {reportError}
        </div>
      )}

      {report && (
        <div
          style={{
            background: '#333',
            padding: '20px',
            borderRadius: '8px',
            borderLeft: '4px solid #007BFF',
          }}
        >
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.7', margin: 0, color: '#eee' }}>{report}</p>
        </div>
      )}

      <p style={{ color: '#777', fontSize: '12px', marginTop: '20px' }}>
        ⚠️ Źródło: Yahoo Finance (raporty) + Google News RSS (newsy), z opóźnieniem względem czasu
        rzeczywistego. To analiza edukacyjna, nie porada inwestycyjna. Agresywne granie pod eventy
        w krótkim terminie niesie wysokie ryzyko.
      </p>
    </div>
  )
}

export default PortfolioNews