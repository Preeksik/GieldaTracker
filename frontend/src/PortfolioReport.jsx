import { useState } from 'react'

const API_URL = 'http://127.0.0.1:8000'

function PortfolioReport() {
  const [report, setReport] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [positionsAnalyzed, setPositionsAnalyzed] = useState(null)

  const generateReport = async () => {
    setLoading(true)
    setError('')
    setReport('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/report`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się wygenerować raportu.')
      }
      const data = await res.json()
      setReport(data.report)
      setPositionsAnalyzed(data.positions_analyzed)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ color: '#fff' }}>
      <h2 style={{ marginBottom: '10px' }}>🧠 Rekomendacje AI dla portfela</h2>
      <p style={{ color: '#aaa', marginBottom: '20px', maxWidth: '700px' }}>
        Analiza uwzględnia trend z ostatnich 3 miesięcy, wolumen i (jeśli dostępne) termin
        najbliższego raportu finansowego dla każdej spółki w Twoim portfelu.
      </p>

      <button
        onClick={generateReport}
        disabled={loading}
        style={{
          padding: '12px 24px',
          background: '#007BFF',
          color: 'white',
          border: 'none',
          borderRadius: '5px',
          cursor: loading ? 'not-allowed' : 'pointer',
          fontSize: '15px',
          marginBottom: '20px',
        }}
      >
        {loading ? 'Analizuję portfel... (może potrwać do minuty)' : '🔍 Wygeneruj raport'}
      </button>

      {error && (
        <div style={{ color: '#FF5252', marginBottom: '20px', background: '#3a1f1f', padding: '12px', borderRadius: '6px' }}>
          {error}
        </div>
      )}

      {report && (
        <div
          style={{
            background: '#333',
            padding: '20px',
            borderRadius: '8px',
            borderLeft: '4px solid #007BFF',
            boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
          }}
        >
          <h3 style={{ marginTop: 0, color: '#007BFF' }}>
            📋 Raport ({positionsAnalyzed} {positionsAnalyzed === 1 ? 'pozycja' : 'pozycji'})
          </h3>
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.7', margin: 0, color: '#eee' }}>
            {report}
          </p>
        </div>
      )}

      <p style={{ color: '#777', fontSize: '12px', marginTop: '20px' }}>
        ⚠️ To automatycznie generowana analiza edukacyjna, nie porada inwestycyjna.
      </p>
    </div>
  )
}

export default PortfolioReport
