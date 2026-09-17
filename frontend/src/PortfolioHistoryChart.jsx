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

// Kolory linii benchmarków — muszą być literalnymi hexami, bo lightweight-charts
// rysuje na canvasie i nie rozwiązuje zmiennych CSS.
const BENCH_COLORS = {
  wig20: '#F59E0B',
  wig: '#A78BFA',
  sp500: '#38BDF8',
  nasdaq: '#F472B6',
  msciworld: '#94A3B8',
}

function filterByRange(series, rangeKey) {
  if (series.length === 0) return []
  if (rangeKey === 'MAX') return series

  const lastDate = new Date(series[series.length - 1].date)
  let cutoff

  if (rangeKey === 'YTD') {
    cutoff = new Date(lastDate.getFullYear(), 0, 1)
  } else {
    const range = RANGES.find((r) => r.key === rangeKey)
    cutoff = new Date(lastDate)
    cutoff.setDate(cutoff.getDate() - (range?.days || 30))
  }

  const filtered = series.filter((h) => new Date(h.date) >= cutoff)
  return filtered.length >= 2 ? filtered : series
}

/**
 * Sprowadza serię do bazy 100 na PIERWSZYM punkcie widocznego zakresu.
 * Bez tego przy zakresach typu YTD czy 30D porównywalibyśmy linie liczone
 * od różnych dat startowych (dnia pierwszego zakupu), co jest matematycznie
 * nieprawidłowe — dwie linie miałyby różne punkty odniesienia.
 */
function rebaseToRange(points) {
  if (points.length === 0) return []
  const base = points[0].value
  if (!base) return points
  return points.map((p) => ({ date: p.date, value: (p.value / base) * 100 }))
}

function PortfolioHistoryChart() {
  const [history, setHistory] = useState([])
  const [events, setEvents] = useState([])
  const [liveHistory, setLiveHistory] = useState([])
  const [range, setRange] = useState('30D')
  const [showCost, setShowCost] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Tryb: 'value' = wartość w PLN, 'bench' = porównanie z rynkiem (baza 100)
  const [mode, setMode] = useState('value')
  const [activeBenchmarks, setActiveBenchmarks] = useState(['wig20', 'sp500'])
  const [benchData, setBenchData] = useState(null)
  const [benchLoading, setBenchLoading] = useState(false)
  const [benchError, setBenchError] = useState('')

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

  const fetchBenchmarks = async (keys) => {
    if (keys.length === 0) {
      setBenchData((d) => (d ? { ...d, benchmarks: {} } : d))
      return
    }
    setBenchLoading(true)
    setBenchError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/benchmark?keys=${keys.join(',')}`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się pobrać danych porównawczych.')
      }
      setBenchData(await res.json())
    } catch (err) {
      setBenchError(err.message)
    } finally {
      setBenchLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [])

  useEffect(() => {
    if (mode === 'bench') fetchBenchmarks(activeBenchmarks)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, activeBenchmarks])

  const toggleBenchmark = (key) => {
    setActiveBenchmarks((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
  }

  const todayStr = new Date().toISOString().split('T')[0]
  const todayLivePoints = liveHistory.filter((h) => h.timestamp?.startsWith(todayStr))
  const isIntraday = mode === 'value' && range === '1D'
  const isBench = mode === 'bench'

  const displayed = isIntraday ? [] : filterByRange(history, range)
  const benchPortfolio = benchData ? rebaseToRange(filterByRange(benchData.portfolio.points, range)) : []

  useEffect(() => {
    if (!containerRef.current) return

    const hasData = isBench
      ? benchPortfolio.length >= 2
      : isIntraday
      ? todayLivePoints.length >= 2
      : displayed.length >= 2
    if (!hasData) return

    const chart = createChart(containerRef.current, {
      layout: { background: { type: 'solid', color: '#0E1524' }, textColor: '#94A3B8', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(148,163,184,0.05)' }, horzLines: { color: 'rgba(148,163,184,0.05)' } },
      rightPriceScale: { borderColor: '#1F2937' },
      timeScale: { borderColor: '#1F2937', timeVisible: isIntraday, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: 1 },
      width: containerRef.current.clientWidth,
      height: 300,
      localization: {
        priceFormatter: (p) => (isBench ? `${(p - 100).toFixed(1)}%` : `${p.toFixed(0)} zł`),
      },
    })

    if (isBench) {
      // Linia bazowa 100 — wszystko powyżej to zysk, poniżej strata
      const baseline = chart.addSeries(LineSeries, {
        color: '#334155',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      baseline.setData(benchPortfolio.map((p) => ({ time: p.date, value: 100 })))

      const portfolioSeries = chart.addSeries(LineSeries, {
        color: '#00F5A0',
        lineWidth: 3,
        priceLineVisible: false,
        title: 'Twój portfel',
      })
      portfolioSeries.setData(benchPortfolio.map((p) => ({ time: p.date, value: p.value })))

      Object.entries(benchData?.benchmarks || {}).forEach(([key, b]) => {
        if (!b.available || b.points.length < 2) return
        const pts = rebaseToRange(filterByRange(b.points, range))
        if (pts.length < 2) return
        const s = chart.addSeries(LineSeries, {
          color: BENCH_COLORS[key] || '#94A3B8',
          lineWidth: 1.5,
          priceLineVisible: false,
          lastValueVisible: false,
          title: b.label,
        })
        s.setData(pts.map((p) => ({ time: p.date, value: p.value })))
      })
    } else {
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
            displayed.filter((h) => h.total_cost !== undefined).map((h) => ({ time: h.date, value: h.total_cost }))
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
  }, [displayed, benchPortfolio, benchData, isIntraday, isBench, todayLivePoints.length, showCost, events, range])

  if (loading) {
    return <div style={{ color: 'var(--text-muted)', marginBottom: '20px' }}>Odtwarzam historię portfela…</div>
  }

  if (error) {
    return <div style={{ color: 'var(--down)', marginBottom: '20px' }}>{error}</div>
  }

  if (history.length < 2) {
    return (
      <div className="hl-panel" style={{ color: 'var(--text-muted)', padding: '15px 20px', marginBottom: '25px' }}>
        📈 Za mało danych historycznych, żeby narysować wykres.
      </div>
    )
  }

  // Nagłówek liczbowy zależy od trybu
  let headline, sub, positive
  let comparisons = []

  if (isBench) {
    const pts = benchPortfolio
    const portfolioReturn = pts.length ? pts[pts.length - 1].value - 100 : 0

    // Porównanie z każdym aktywnym indeksem — liczone na tym samym, widocznym zakresie
    comparisons = Object.entries(benchData?.benchmarks || {})
      .filter(([, b]) => b.available && b.points.length >= 2)
      .map(([key, b]) => {
        const bp = rebaseToRange(filterByRange(b.points, range))
        const benchReturn = bp.length ? bp[bp.length - 1].value - 100 : 0
        return {
          key,
          label: b.label,
          benchReturn,
          diff: portfolioReturn - benchReturn, // w punktach procentowych
        }
      })

    // Kolor i werdykt zależą od tego, czy bijesz rynek — NIE od tego, czy masz zysk.
    // Bez tego zielony napis przy zysku sugerowałby sukces, nawet gdy indeks urósł mocniej.
    const beaten = comparisons.filter((c) => c.diff >= 0).length
    positive = comparisons.length === 0 ? portfolioReturn >= 0 : beaten === comparisons.length

    headline = `${portfolioReturn >= 0 ? '+' : ''}${portfolioReturn.toFixed(2)}%`

    if (comparisons.length === 0) {
      sub = 'stopa zwrotu portfela (TWR)'
    } else if (beaten === comparisons.length) {
      sub = `bijesz rynek — lepiej od ${comparisons.length === 1 ? comparisons[0].label : 'wszystkich wybranych indeksów'}`
    } else if (beaten === 0) {
      const worst = comparisons.reduce((a, b) => (a.diff < b.diff ? a : b))
      sub = `rynek Cię bije — ${Math.abs(worst.diff).toFixed(1)} pp gorzej niż ${worst.label}`
    } else {
      sub = `mieszany wynik — lepiej od ${beaten} z ${comparisons.length} indeksów`
    }
  } else {
    const series = isIntraday ? todayLivePoints : displayed
    const first = series[0]?.total_value
    const last = series[series.length - 1]?.total_value
    const change = first !== undefined && last !== undefined ? last - first : 0
    positive = change >= 0
    headline = `${last?.toFixed(2)} PLN`
    sub = `${positive ? '▲' : '▼'} ${Math.abs(change).toFixed(2)} PLN (${first ? ((change / first) * 100).toFixed(2) : '0.00'}%)`
  }

  const chip = (active) => ({
    padding: '5px 12px',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#04120C' : 'var(--text-dim)',
    border: `1px solid ${active ? 'var(--accent)' : 'var(--border-bright)'}`,
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '12px',
    fontWeight: 600,
  })

  return (
    <div className="hl-panel" style={{ marginBottom: '25px', padding: '18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <div style={{ color: 'var(--text-dim)', fontSize: '13px', marginBottom: '2px' }}>
            {isBench ? 'Portfel vs rynek' : 'Wartość portfela'}
          </div>
          <div style={{ color: positive ? 'var(--up)' : 'var(--down)', fontSize: '26px', fontWeight: 'bold' }}>
            {headline}
          </div>
          <div style={{ color: 'var(--text-dim)', fontSize: '13px', marginTop: '2px' }}>{sub}</div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-end' }}>
          {/* Przełącznik trybu */}
          <div style={{ display: 'flex', gap: '5px' }}>
            <button onClick={() => setMode('value')} style={chip(mode === 'value')}>Wartość</button>
            <button onClick={() => setMode('bench')} style={chip(mode === 'bench')}>vs Rynek</button>
          </div>
          {/* Zakresy */}
          <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {RANGES.filter((r) => !(isBench && r.key === '1D')).map((r) => (
              <button key={r.key} onClick={() => setRange(r.key)} style={chip(range === r.key)}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isBench && (
        <div style={{ display: 'flex', gap: '7px', marginBottom: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ color: 'var(--text-dim)', fontSize: '11.5px', textTransform: 'uppercase', letterSpacing: '0.6px', fontWeight: 600 }}>
            Porównaj z
          </span>
          {Object.entries(benchData?.available_keys || { wig20: 'WIG20', sp500: 'S&P 500', nasdaq: 'NASDAQ 100' }).map(([key, label]) => {
            const on = activeBenchmarks.includes(key)
            const unavailable = benchData?.benchmarks?.[key]?.available === false
            return (
              <button
                key={key}
                onClick={() => toggleBenchmark(key)}
                title={unavailable ? benchData.benchmarks[key].reason : undefined}
                style={{
                  ...chip(false),
                  borderColor: on ? BENCH_COLORS[key] : 'var(--border-bright)',
                  color: on ? BENCH_COLORS[key] : 'var(--text-dim)',
                  opacity: unavailable ? 0.45 : 1,
                  textDecoration: unavailable ? 'line-through' : 'none',
                }}
              >
                {on && <span style={{ marginRight: '5px' }}>●</span>}
                {label}
              </button>
            )
          })}
          {benchLoading && <span style={{ color: 'var(--text-dim)', fontSize: '12px' }}>ładuję…</span>}
        </div>
      )}

      {benchError && isBench && (
        <div style={{ color: 'var(--down)', fontSize: '13px', marginBottom: '10px' }}>{benchError}</div>
      )}

      {isIntraday && todayLivePoints.length < 2 ? (
        <div style={{ color: 'var(--text-muted)', background: 'var(--bg-deep)', padding: '25px', borderRadius: 'var(--radius-sm)', textAlign: 'center' }}>
          Za mało punktów z dzisiaj — dane wewnątrzdniowe zbierają się co ~30 min, tylko gdy backend działa.
        </div>
      ) : (
        <div ref={containerRef} style={{ width: '100%', height: '300px', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }} />
      )}

      {isBench && comparisons.length > 0 && (
        <div
          style={{
            display: 'flex',
            gap: '10px',
            marginTop: '14px',
            flexWrap: 'wrap',
          }}
        >
          {comparisons.map((c) => {
            const won = c.diff >= 0
            return (
              <div
                key={c.key}
                style={{
                  flex: '1 1 190px',
                  background: 'var(--bg-deep)',
                  border: `1px solid ${won ? 'rgba(0,245,160,0.25)' : 'rgba(255,91,127,0.25)'}`,
                  borderRadius: 'var(--radius-sm)',
                  padding: '11px 14px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', marginBottom: '5px' }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: BENCH_COLORS[c.key] || '#94A3B8' }} />
                  <span style={{ fontSize: '12.5px', color: 'var(--text-muted)' }}>{c.label}</span>
                </div>
                <div style={{ fontSize: '13px', color: 'var(--text-dim)' }}>
                  indeks: <span className="hl-num">{c.benchReturn >= 0 ? '+' : ''}{c.benchReturn.toFixed(2)}%</span>
                </div>
                <div style={{ fontSize: '14px', fontWeight: 700, color: won ? 'var(--up)' : 'var(--down)', marginTop: '3px' }}>
                  {won ? '▲ wygrywasz' : '▼ przegrywasz'} o {Math.abs(c.diff).toFixed(2)} pp
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '10px', flexWrap: 'wrap', gap: '8px' }}>
        {!isBench && !isIntraday && (
          <label style={{ color: 'var(--text-dim)', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
            <input type="checkbox" checked={showCost} onChange={(e) => setShowCost(e.target.checked)} />
            Pokaż linię wpłaconego kapitału
          </label>
        )}
        <div style={{ color: 'var(--text-dim)', fontSize: '11px', marginLeft: 'auto' }}>
          {isBench
            ? 'Wszystkie linie od bazy 100 na początku wybranego zakresu. Portfel liczony metodą TWR — dopłaty kapitału nie zawyżają wyniku.'
            : '🔵 zakup · 🔴 sprzedaż · szara linia = wpłacone'}
        </div>
      </div>
    </div>
  )
}

export default PortfolioHistoryChart