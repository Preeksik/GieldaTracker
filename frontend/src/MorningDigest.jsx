import { useState } from 'react'
import MarkdownView from './MarkdownView'
import { StepLoader } from './Loader'

const API_URL = 'http://127.0.0.1:8000'

function MorningDigest() {
  const [report, setReport] = useState('')
  const [snapshot, setSnapshot] = useState([])
  const [generatedAt, setGeneratedAt] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const runDigest = async () => {
    setLoading(true)
    setError('')
    setReport('')
    try {
      const res = await fetch(`${API_URL}/api/digest/morning`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się wygenerować porannego briefingu.')
      }
      const data = await res.json()
      setReport(data.report)
      setSnapshot(data.market_snapshot || [])
      setGeneratedAt(data.generated_at)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>☕ Poranny Briefing Inwestora</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Jednym kliknięciem: stan rynków Azji i USA z nocy, kluczowe wydarzenia rynkowe na dziś
        i zbliżające się wyniki finansowe spółek z Twojego portfela i watchlisty.
      </p>

      <button
        onClick={runDigest}
        disabled={loading} className="hl-btn hl-btn-primary"
        style={{
          padding: '12px 24px',
          background: 'var(--accent)',
          color: 'white',
          border: 'none',
          borderRadius: 'var(--radius-sm)',
          cursor: loading ? 'not-allowed' : 'pointer',
          fontSize: '15px',
          marginBottom: '20px',
        }}
      >
        {loading ? 'Zbieram briefing...' : '☕ Generuj briefing poranny'}
      </button>

      {loading && (
        <div style={{ marginBottom: '20px' }}>
          <StepLoader
            title="Zbieram poranny briefing"
            steps={[
              'Sprawdzam zamknięcia Azji i USA',
              'Szukam świeżych nagłówków rynkowych',
              'Sprawdzam terminy raportów finansowych',
              'Gemini układa briefing',
            ]}
          />
        </div>
      )}

      {error && (
        <div style={{ color: 'var(--down)', marginBottom: '20px', background: 'rgba(255,91,127,0.08)', padding: '12px', borderRadius: '6px' }}>
          {error}
        </div>
      )}

      {snapshot.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
            gap: '10px',
            marginBottom: '20px',
          }}
        >
          {snapshot.map((s) => (
            <div
              key={s.label}
              className="hl-panel"
              style={{ padding: '12px 14px' }}
            >
              <div style={{ fontSize: '11px', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>
                {s.region}
              </div>
              <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '4px' }}>{s.label}</div>
              {s.price === null ? (
                <div style={{ fontSize: '13px', color: 'var(--text-dim)' }}>brak danych</div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                  <span className="hl-num" style={{ fontSize: '15px' }}>{s.price}</span>
                  {s.change_pct !== null && (
                    <span className={s.change_pct >= 0 ? 'hl-up' : 'hl-down'} style={{ fontSize: '13px', fontWeight: 600 }}>
                      {s.change_pct >= 0 ? '▲' : '▼'} {Math.abs(s.change_pct)}%
                    </span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {report && (
        <div
          style={{
            background: 'var(--bg-elevated)',
            padding: '20px',
            borderRadius: 'var(--radius)',
            borderLeft: '4px solid var(--accent)',
            marginBottom: '16px',
          }}
        >
          <MarkdownView>{report}</MarkdownView>
        </div>
      )}

      {generatedAt && (
        <p style={{ color: 'var(--text-dim)', fontSize: '12px' }}>
          Wygenerowano: {new Date(generatedAt).toLocaleString('pl-PL')}
        </p>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '12px' }}>
        ⚠️ Notowania indeksów i nagłówki z opóźnieniem względem czasu rzeczywistego.
        To analiza edukacyjna, nie porada inwestycyjna.
      </p>
    </div>
  )
}

export default MorningDigest
