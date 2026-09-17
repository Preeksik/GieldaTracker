import { useState, useEffect, useRef } from 'react'
import { createChart, LineSeries, LineStyle, createSeriesMarkers } from 'lightweight-charts'

const API_URL = 'http://127.0.0.1:8000'

const RANGES = [
  { key: '1D', label: '1D' },
  { key: '7D', label: '7D', days: 7 },
  { key: '30D', label: '30D', days: 30 },
  { key: 'YTD', label: 'YTD' },
  { key: '1R', label: '1 rok', days: 365 },
  { key: '5L', label: '5 lat', days: 365 * 5 },
  { key: 'MAX', label: 'Max' },
]

function filterByRange(history, rangeKey) {
  if (history.length === 0) return []
  if (rangeKey === 'MAX') return history

  const lastDate = new Date(history[history.length - 1].date)
  let cutoff

  if (rangeKey === 'YTD') {
    cutoff = new Date(lastDate.getFullYear(), 0, 1)
  } else {
    const range = RANGES.find((r) => r.key === rangeKey)
    cutoff = new Date(lastDate)
    cutoff.setDate(cutoff.getDate() - (range?.days || 30))
  }

  const filtered = history.filter((h) => new Date(h.date) >= cutoff)
  // Jeśli w wybranym oknie jest mniej niż 2 punkty, pokazujemy całość zamiast pustego wykresu
  return filtered.length >= 2 ? filtered : history
}

function PortfolioHistoryChart() {
  const [history, setHistory] = useState([])
  const [events, setEvents] = useState([])
  const [liveHistory, setLiveHistory] = useState([])
  const [range, setRange] = useState('30D')
  const [showCost, setShowCost] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const containerRef = useRef(null)

  const fetchAll = async () => {
    setLoading(true)
    setError('')
    try {
      const [fullRes, liveRes] = await Promise.all([
        fetch(`${API_URL}/api/portfolio/history/full`),
        fetch(`${API_URL}/api/portfolio/history`),
      ])
      if (!fullRes.ok) {
        const errData = await fullRes.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się odtworzyć historii portfela.')
      }
      const fullData = await fullRes.json()
      setHistory(fullData.history || [])
      setEvents(fullData.events || [])

      if (liveRes.ok) {
        const liveData = await liveRes.json()
        setLiveHistory(liveData.history || [])
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [])

  const todayStr = new Date().toISOString().split('T')[0]
  const todayLivePoints = liveHistory.filter((h) => h.timestamp?.startsWith(todayStr))
  const isIntraday = range === '1D'
  const displayed = isIntraday ? [] : filterByRange(history, range)

  useEffect(() => {
    if (!containerRef.current) return
    const hasData = isIntraday ? todayLivePoints.length >= 2 : displayed.length >= 2
    if (!hasData) return

    const chart = createChart(containerRef.current, {
      layout: { background: { type: 'solid', color: '#0E1524' }, textColor: '#94A3B8', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(148,163,184,0.055)' }, horzLines: { color: 'rgba(148,163,184,0.055)' } },
      rightPriceScale: { borderColor: '#1F2937' },
      timeScale: { borderColor: '#1F2937', timeVisible: isIntraday, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: 1 },
      width: containerRef.current.clientWidth,
      height: 300,
      localization: {
        priceFormatter: (p) => `${p.toFixed(0)} zł`,
      },
    })

    const valueSeries = chart.addSeries(LineSeries, {
      color: '#00F5A0',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'Wartość',
    })

    if (isIntraday) {
      valueSeries.setData(
        todayLivePoints.map((h) => ({
          time: Math.floor(new Date(h.timestamp.replace(' ', 'T')).getTime() / 1000),
          value: h.total_value,
        }))
      )
    } else {
      valueSeries.setData(displayed.map((h) => ({ time: h.date, value: h.total_value })))

      if (showCost && displayed.some((h) => h.total_cost !== undefined)) {
        const costSeries = chart.addSeries(LineSeries, {
          color: '#64748B',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
          title: 'Wpłacone',
        })
        costSeries.setData(
          displayed
            .filter((h) => h.total_cost !== undefined)
            .map((h) => ({ time: h.date, value: h.total_cost }))
        )
      }

      const visibleEvents = events.filter((e) => displayed.some((d) => d.date === e.date))
      if (visibleEvents.length > 0) {
        createSeriesMarkers(
          valueSeries,
          visibleEvents.map((e) => ({
            time: e.date,
            position: e.type === 'sell' ? 'aboveBar' : 'belowBar',
            color: e.type === 'sell' ? '#FF5B7F' : '#06B6D4',
            shape: e.type === 'sell' ? 'arrowDown' : 'arrowUp',
            text: `${e.type === 'sell' ? '−' : '+'}${e.ticker}`,
          }))
        )
      }
    }

    chart.timeScale().fitContent()

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      try {
        chart.remove()
      } catch (e) {
        // wykres mógł już zostać usunięty (React StrictMode w dev)
      }
    }
  }, [displayed, isIntraday, todayLivePoints.length, showCost, events])

  if (loading) {
    return <div style={{ color: 'var(--text-muted)', marginBottom: '20px' }}>Odtwarzam historię portfela...</div>
  }

  if (error) {
    return <div style={{ color: 'var(--down)', marginBottom: '20px' }}>{error}</div>
  }

  if (history.length < 2) {
    return (
      <div style={{ color: 'var(--text-muted)', background: 'var(--bg-panel)', padding: '15px 20px', borderRadius: '10px', marginBottom: '25px' }}>
        📈 Za mało danych historycznych, żeby narysować wykres.
      </div>
    )
  }

  const series = isIntraday ? todayLivePoints : displayed
  const first = series[0]?.total_value
  const last = series[series.length - 1]?.total_value
  const change = first !== undefined && last !== undefined ? last - first : null
  const changePct = first ? (change / first) * 100 : null
  const positive = change >= 0

  const btnStyle = (active) => ({
    padding: '5px 12px',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? 'var(--text)' : 'var(--text-dim)',
    border: `1px solid ${active ? 'var(--accent)' : '#3a3a4a'}`,
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '12px',
  })

  return (
    <div style={{ marginBottom: '25px', background: 'var(--bg-panel)', padding: '18px', borderRadius: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px', flexWrap: 'wrap', gap: '10px' }}>
        <div>
          <div style={{ color: 'var(--text-dim)', fontSize: '13px', marginBottom: '2px' }}>Wartość portfela</div>
          <div style={{ color: 'var(--text)', fontSize: '26px', fontWeight: 'bold' }}>
            {last?.toFixed(2)} PLN
          </div>
          {change !== null && (
            <div style={{ color: positive ? 'var(--up)' : 'var(--down)', fontSize: '14px', marginTop: '2px' }}>
              {positive ? '▲' : '▼'} {Math.abs(change).toFixed(2)} PLN ({changePct.toFixed(2)}%)
              <span style={{ color: 'var(--text-dim)' }}> · {RANGES.find((r) => r.key === range)?.label}</span>
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
          {RANGES.map((r) => (
            <button className="hl-btn" key={r.key} onClick={() => setRange(r.key)} style={btnStyle(range === r.key)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {isIntraday && todayLivePoints.length < 2 ? (
        <div style={{ color: 'var(--text-muted)', background: 'var(--bg-panel)', padding: '25px', borderRadius: 'var(--radius)', textAlign: 'center' }}>
          Za mało punktów z dzisiaj — dane wewnątrzdniowe zbierają się co ~30 min, tylko gdy backend działa.
        </div>
      ) : (
        <div ref={containerRef} style={{ width: '100%', height: '300px', borderRadius: 'var(--radius)', overflow: 'hidden' }} />
      )}

      {!isIntraday && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '10px', flexWrap: 'wrap', gap: '8px' }}>
          <label style={{ color: 'var(--text-dim)', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
            <input type="checkbox" checked={showCost} onChange={(e) => setShowCost(e.target.checked)} />
            Pokaż linię wpłaconego kapitału
          </label>
          <div style={{ color: 'var(--text-dim)', fontSize: '11px' }}>
            🔵 zakup · 🔴 sprzedaż · szara linia = wpłacone
          </div>
        </div>
      )}
    </div>
  )
}

export default PortfolioHistoryChart