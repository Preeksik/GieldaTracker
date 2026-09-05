import { useState } from 'react'

const API_URL = 'http://127.0.0.1:8000'

function PortfolioNews() {
  const [report, setReport] = useState('')
  const [checked, setChecked] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const fetchNews = async () => {
    setLoading(true)
    setError('')
    setReport('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/news`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się pobrać newsów.')
      }
      const data = await res.json()
      setReport(data.report)
      setChecked(data.tickers_checked)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ color: '#fff' }}>
      <h2 style={{ marginBottom: '10px' }}>🔥 Gorące newsy</h2>
      <p style={{ color: '#aaa', marginBottom: '20px', maxWidth: '700px' }}>
        Pobiera najświeższe nagłówki (Google News) dla spółek z Twojego portfela i prosi AI
        o ocenę, czy coś z tego wygląda na realny katalizator ruchu kursu.
      </p>

      <button
        onClick={fetchNews}
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
        {loading ? 'Sprawdzam newsy...' : '📡 Sprawdź newsy'}
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
          }}
        >
          <h3 style={{ marginTop: 0, color: '#007BFF' }}>
            📋 Przegląd newsów ({checked.length} {checked.length === 1 ? 'spółka' : 'spółek'}: {checked.join(', ')})
          </h3>
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.7', margin: 0, color: '#eee' }}>{report}</p>
        </div>
      )}

      <p style={{ color: '#777', fontSize: '12px', marginTop: '20px' }}>
        ⚠️ Źródło: Google News RSS z opóźnieniem względem czasu rzeczywistego. To analiza edukacyjna, nie porada inwestycyjna.
      </p>
    </div>
  )
}

export default PortfolioNews
