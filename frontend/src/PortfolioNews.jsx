import { useState, useEffect } from 'react'
import MarkdownView from './MarkdownView'
import TickerSearch from './TickerSearch'
import { StepLoader } from './Loader'

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
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>🔥 Radar katalizatorów</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Watchlista niezależna od portfela - obserwuj spółki pod kątem nadchodzących raportów
        finansowych i dużych wydarzeń, niezależnie czy je posiadasz. Pod agresywne, krótkoterminowe granie.
      </p>

      {/* Zarządzanie watchlistą */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '15px', flexWrap: 'wrap' }}>
        <TickerSearch
          value={newTicker}
          onChange={setNewTicker}
          onEnter={addTicker}
          placeholder="Dodaj spółkę — nazwa albo ticker"
          style={{ width: '280px' }}
        />
        <button
          onClick={addTicker} className="hl-btn hl-btn-primary"
          style={{
            padding: '10px 20px',
            background: 'var(--accent)',
            color: 'white',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            cursor: 'pointer',
          }}
        >
          Dodaj do watchlisty
        </button>
      </div>

      {watchlistError && <div style={{ color: 'var(--down)', marginBottom: '15px' }}>{watchlistError}</div>}

      {watchlistLoading ? (
        <div style={{ color: 'var(--text-muted)', marginBottom: '20px' }}>Ładowanie watchlisty...</div>
      ) : (
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '25px' }}>
          {tickers.length === 0 && <span style={{ color: 'var(--text-dim)' }}>Watchlista jest pusta.</span>}
          {tickers.map((t) => (
            <span
              key={t}
              style={{
                background: 'var(--bg-elevated)',
                padding: '6px 10px',
                borderRadius: '15px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '13px',
              }}
            >
              {t}
              <button className="hl-btn"
                onClick={() => removeTicker(t)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--down)',
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
        disabled={reportLoading || tickers.length === 0} className="hl-btn hl-btn-primary"
        style={{
          padding: '12px 24px',
          background: 'var(--accent)',
          color: 'white',
          border: 'none',
          borderRadius: 'var(--radius-sm)',
          cursor: reportLoading || tickers.length === 0 ? 'not-allowed' : 'pointer',
          fontSize: '15px',
          marginBottom: '20px',
        }}
      >
        {reportLoading ? 'Skanuję watchlistę... (może potrwać do minuty)' : '📡 Sprawdź nadchodzące wydarzenia'}
      </button>

      {reportLoading && (
        <div style={{ marginBottom: '20px' }}>
          <StepLoader
            title="Skanuję watchlistę"
            steps={['Sprawdzam terminy raportów finansowych', 'Pobieram świeże nagłówki z rynku', 'Filtruję szum od realnych katalizatorów', 'Układam radar wg priorytetu']}
            intervalMs={1100}
          />
        </div>
      )}

      {reportError && (
        <div style={{ color: 'var(--down)', marginBottom: '20px', background: 'rgba(255,91,127,0.08)', padding: '12px', borderRadius: '6px' }}>
          {reportError}
        </div>
      )}

      {report && (
        <div
          style={{
            background: 'var(--bg-elevated)',
            padding: '20px',
            borderRadius: 'var(--radius)',
            borderLeft: '4px solid var(--accent)',
          }}
        >
          <MarkdownView>{report}</MarkdownView>
        </div>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '20px' }}>
        ⚠️ Źródło: Yahoo Finance (raporty) + Google News RSS (newsy), z opóźnieniem względem czasu
        rzeczywistego. To analiza edukacyjna, nie porada inwestycyjna. Agresywne granie pod eventy
        w krótkim terminie niesie wysokie ryzyko.
      </p>
    </div>
  )
}

export default PortfolioNews