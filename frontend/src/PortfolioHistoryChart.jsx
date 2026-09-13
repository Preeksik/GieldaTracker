import { useState, useEffect, useRef } from 'react'
import { createChart, LineSeries } from 'lightweight-charts'

const API_URL = 'http://127.0.0.1:8000'

function PortfolioHistoryChart() {
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [snapshotLoading, setSnapshotLoading] = useState(false)

  const containerRef = useRef(null)
  const chartInstanceRef = useRef(null)

  const fetchHistory = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/history`)
      if (!res.ok) throw new Error('Nie udało się pobrać historii portfela.')
      const data = await res.json()
      setHistory(data.history)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const forceSnapshot = async () => {
    setSnapshotLoading(true)
    try {
      const res = await fetch(`${API_URL}/api/portfolio/history/snapshot-now`, { method: 'POST' })
      if (!res.ok) throw new Error('Nie udało się dodać punktu do historii.')
      const data = await res.json()
      setHistory(data.history)
    } catch (err) {
      setError(err.message)
    } finally {
      setSnapshotLoading(false)
    }
  }

  useEffect(() => {
    fetchHistory()
  }, [])

  useEffect(() => {
    if (!containerRef.current || history.length === 0) return

    const chart = createChart(containerRef.current, {
      layout: { background: { type: 'solid', color: '#1E1E2F' }, textColor: '#DDD' },
      grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
      width: containerRef.current.clientWidth,
      height: 260,
    })

    const series = chart.addSeries(LineSeries, {
      color: '#4CAF50',
      lineWidth: 2,
      priceLineVisible: false,
    })

    // lightweight-charts wymaga rosnących, unikalnych znaczników czasu (sekundy Unix)
    const seen = new Set()
    const data = []
    history.forEach((h) => {
      const time = Math.floor(new Date(h.timestamp.replace(' ', 'T')).getTime() / 1000)
      if (!seen.has(time)) {
        seen.add(time)
        data.push({ time, value: h.total_value })
      }
    })

    series.setData(data)
    chart.timeScale().fitContent()
    chartInstanceRef.current = chart

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      try {
        chart.remove()
      } catch (e) {
        // wykres mógł już zostać usunięty (React StrictMode w dev) - ignorujemy
      }
      if (chartInstanceRef.current === chart) {
        chartInstanceRef.current = null
      }
    }
  }, [history])

  if (loading) {
    return <div style={{ color: '#aaa', marginBottom: '20px' }}>Ładowanie historii portfela...</div>
  }

  if (error) {
    return <div style={{ color: '#FF5252', marginBottom: '20px' }}>{error}</div>
  }

  if (history.length < 2) {
    return (
      <div
        style={{
          color: '#aaa',
          background: '#2a2a2a',
          padding: '15px 20px',
          borderRadius: '8px',
          marginBottom: '25px',
        }}
      >
        📈 Historia wartości portfela dopiero się buduje (zbieramy punkt co ~30 minut, gdy backend
        jest uruchomiony). Potrzeba przynajmniej 2 punktów, żeby narysować wykres.
        <div style={{ marginTop: '10px' }}>
          <button
            onClick={forceSnapshot}
            disabled={snapshotLoading}
            style={{
              padding: '8px 16px',
              background: '#007BFF',
              color: 'white',
              border: 'none',
              borderRadius: '5px',
              cursor: snapshotLoading ? 'not-allowed' : 'pointer',
              fontSize: '13px',
            }}
          >
            {snapshotLoading ? 'Dodaję...' : '➕ Dodaj punkt teraz (do testów)'}
          </button>
        </div>
      </div>
    )
  }

  const first = history[0].total_value
  const last = history[history.length - 1].total_value
  const change = last - first
  const changePct = first ? (change / first) * 100 : 0

  return (
    <div style={{ marginBottom: '25px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', flexWrap: 'wrap', gap: '10px' }}>
        <h3 style={{ margin: 0, color: '#fff' }}>📈 Wartość portfela w czasie</h3>
        <div style={{ color: change >= 0 ? '#4CAF50' : '#FF5252', fontWeight: 'bold' }}>
          {change >= 0 ? '+' : ''}
          {change.toFixed(2)} PLN ({changePct.toFixed(2)}%) od {history[0].timestamp.split(' ')[0]}
        </div>
      </div>
      <div
        ref={containerRef}
        style={{ width: '100%', height: '260px', borderRadius: '8px', overflow: 'hidden', backgroundColor: '#1E1E2F' }}
      />
    </div>
  )
}

export default PortfolioHistoryChart