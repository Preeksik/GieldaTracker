import { useState } from 'react'

const API_URL = 'http://127.0.0.1:8000'

/*
  Przycisk "Wyczyść cały portfel" z dwustopniowym potwierdzeniem:
    1) co zostanie usunięte (z liczbami) + wybór: historia sprzedaży, wykres wartości,
    2) wpisanie słowa USUŃ -> definitywne usunięcie.
  Backend przed usunięciem robi automatyczną kopię zapasową (do przywrócenia w panelu kopii).
  onCleared - wywoływane po udanym usunięciu (np. fetchPortfolio), żeby odświeżyć widok.
*/
function ClearPortfolio({ onCleared }) {
  const [step, setStep] = useState(0) // 0 = przycisk, 1 = co usuwamy, 2 = wpisz USUŃ
  const [preview, setPreview] = useState(null)
  const [includeSales, setIncludeSales] = useState(true)
  const [includeHistory, setIncludeHistory] = useState(true)
  const [word, setWord] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  const reset = () => {
    setStep(0)
    setWord('')
    setError('')
  }

  const start = async () => {
    setError('')
    setResult(null)
    try {
      const res = await fetch(`${API_URL}/api/portfolio/clear/preview`)
      if (!res.ok) throw new Error('Nie udało się sprawdzić zawartości portfela.')
      setPreview(await res.json())
      setStep(1)
    } catch (err) {
      setError(err.message)
    }
  }

  const confirmDelete = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/clear`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: word, include_sales: includeSales, include_history: includeHistory }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Nie udało się wyczyścić portfela.')
      setResult(data)
      reset()
      if (onCleared) onCleared()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const box = {
    marginTop: '12px',
    padding: '14px',
    border: '1px solid var(--down)',
    borderRadius: 'var(--radius)',
    background: 'var(--bg-elevated)',
  }
  const dangerBtn = {
    padding: '8px 14px',
    background: 'var(--down)',
    color: 'white',
    border: 'none',
    borderRadius: 'var(--radius-sm)',
    cursor: 'pointer',
  }
  const plainBtn = {
    padding: '8px 14px',
    background: 'transparent',
    color: 'var(--text-dim)',
    border: '1px solid var(--text-dim)',
    borderRadius: 'var(--radius-sm)',
    cursor: 'pointer',
  }
  const word_ok = word.trim().toUpperCase() === (preview?.confirm_word || 'USUŃ')

  return (
    <div style={{ marginTop: '20px' }}>
      {step === 0 && (
        <button
          className="hl-btn"
          onClick={start}
          style={{ ...plainBtn, color: 'var(--down)', borderColor: 'var(--down)' }}
        >
          🗑 Wyczyść cały portfel
        </button>
      )}

      {step === 1 && preview && (
        <div style={box}>
          <strong style={{ color: 'var(--down)' }}>Na pewno wyczyścić cały portfel?</strong>
          <div style={{ marginTop: '8px' }}>
            Zostanie usunięte: <strong>{preview.positions}</strong> pozycji w portfelu.
          </div>
          <label style={{ display: 'block', marginTop: '8px', cursor: 'pointer' }}>
            <input type="checkbox" checked={includeSales} onChange={(e) => setIncludeSales(e.target.checked)} />{' '}
            także historia sprzedaży ({preview.sales} transakcji, w tym dane do PIT-38)
          </label>
          <label style={{ display: 'block', marginTop: '4px', cursor: 'pointer' }}>
            <input type="checkbox" checked={includeHistory} onChange={(e) => setIncludeHistory(e.target.checked)} />{' '}
            także wykres wartości portfela ({preview.history_points} zapisanych punktów)
          </label>
          <div style={{ marginTop: '8px', color: 'var(--text-dim)', fontSize: '12px' }}>
            Watchlista, alerty, dziennik porad i ustawienia zostają. Przed usunięciem aplikacja zrobi
            automatyczną kopię zapasową - w razie pomyłki przywrócisz ją w panelu kopii zapasowych.
          </div>
          <div style={{ display: 'flex', gap: '10px', marginTop: '12px' }}>
            <button className="hl-btn" style={dangerBtn} onClick={() => setStep(2)}>
              Tak, dalej
            </button>
            <button className="hl-btn" style={plainBtn} onClick={reset}>
              Anuluj
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div style={box}>
          <strong style={{ color: 'var(--down)' }}>Ostatni krok - tego nie da się cofnąć jednym kliknięciem.</strong>
          <div style={{ marginTop: '8px' }}>
            Wpisz <strong>{preview?.confirm_word || 'USUŃ'}</strong>, żeby definitywnie usunąć portfel
            {includeSales ? ', historię sprzedaży' : ''}
            {includeHistory ? ' i wykres wartości' : ''}.
          </div>
          <input
            autoFocus
            value={word}
            onChange={(e) => setWord(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && word_ok && !loading) confirmDelete()
            }}
            placeholder={preview?.confirm_word || 'USUŃ'}
            style={{
              marginTop: '8px',
              padding: '8px',
              width: '160px',
              background: 'var(--bg)',
              color: 'inherit',
              border: '1px solid var(--text-dim)',
              borderRadius: 'var(--radius-sm)',
            }}
          />
          <div style={{ display: 'flex', gap: '10px', marginTop: '12px' }}>
            <button
              className="hl-btn"
              style={{ ...dangerBtn, opacity: word_ok && !loading ? 1 : 0.4, cursor: word_ok ? 'pointer' : 'not-allowed' }}
              disabled={!word_ok || loading}
              onClick={confirmDelete}
            >
              {loading ? 'Usuwam…' : 'Usuń definitywnie'}
            </button>
            <button className="hl-btn" style={plainBtn} onClick={reset} disabled={loading}>
              Anuluj
            </button>
          </div>
        </div>
      )}

      {error && <div style={{ color: 'var(--down)', marginTop: '10px' }}>{error}</div>}

      {result && (
        <div style={{ marginTop: '10px', color: 'var(--up)' }}>
          ✅ Portfel wyczyszczony: {result.removed.positions} pozycji
          {result.removed.sales ? `, ${result.removed.sales} sprzedaży` : ''}
          {result.removed.history_points ? `, ${result.removed.history_points} punktów wykresu` : ''}.
          <div style={{ color: 'var(--text-dim)', fontSize: '12px' }}>
            Kopia zapasowa sprzed usunięcia: {result.safety_snapshot}
          </div>
        </div>
      )}
    </div>
  )
}

export default ClearPortfolio
