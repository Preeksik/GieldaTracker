import { useState, useEffect, useRef } from 'react'
import { createChart, CandlestickSeries } from 'lightweight-charts'
import PortfolioTracker from './PortfolioTracker'
import PortfolioReport from './PortfolioReport'
import PortfolioNews from './PortfolioNews'
import DividendCalendar from './DividendCalendar'
import PriceAlerts from './PriceAlerts'
import SalesHistory from './SalesHistory'
import BackupPanel from './BackupPanel'
import MarkdownView from './MarkdownView'
import { BrandLockup } from './Logo'
import { StepLoader, Spinner } from './Loader'
import './theme.css'

const TABS = [
  { key: 'analiza', icon: '◈', label: 'Analiza' },
  { key: 'portfel', icon: '◼', label: 'Portfel' },
  { key: 'rekomendacje', icon: '◆', label: 'Analiza i pytania' },
  { key: 'newsy', icon: '▲', label: 'Radar' },
  { key: 'dywidendy', icon: '●', label: 'Dywidendy' },
  { key: 'alerty', icon: '◉', label: 'Alerty' },
  { key: 'sprzedaze', icon: '▼', label: 'Sprzedaże' },
  { key: 'dane', icon: '⬢', label: 'Dane' },
]

const HORIZONS = [
  { key: 'krotki', label: 'Krótkoterminowo' },
  { key: 'sredni', label: 'Średnioterminowo' },
  { key: 'dlugi', label: 'Długoterminowo' },
]

function App() {
  const [ticker, setTicker] = useState('CDR.WA')
  const [question, setQuestion] = useState('Jak oceniasz aktualny trend spółki?')
  const [horizon, setHorizon] = useState('sredni')
  const [chartData, setChartData] = useState([])
  const [aiAnalysis, setAiAnalysis] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('analiza')
  const [tabKey, setTabKey] = useState(0) // wymusza re-animację przy zmianie zakładki

  const chartContainerRef = useRef(null)
  const chartInstanceRef = useRef(null)

  const switchTab = (key) => {
    if (key === activeTab) return
    setActiveTab(key)
    setTabKey((k) => k + 1)
  }

  const analyzeStock = async () => {
    setLoading(true)
    setError('')
    setAiAnalysis('')

    try {
      const response = await fetch('http://127.0.0.1:8000/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker,
          question,
          days: horizon === 'krotki' ? 14 : horizon === 'dlugi' ? 90 : 30,
          horizon,
        }),
      })

      if (!response.ok) throw new Error('Błąd pobierania danych z backendu.')

      const data = await response.json()
      setChartData(data.chart_data)
      setAiAnalysis(data.ai_analysis)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!chartContainerRef.current || chartData.length === 0) return

    const chart = createChart(chartContainerRef.current, {
      layout: { background: { type: 'solid', color: '#0B1120' }, textColor: '#94A3B8', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(148,163,184,0.055)' }, horzLines: { color: 'rgba(148,163,184,0.055)' } },
      rightPriceScale: { borderColor: '#1F2937' },
      timeScale: { borderColor: '#1F2937' },
      crosshair: { mode: 1, vertLine: { color: '#10B981', labelBackgroundColor: '#059669' }, horzLine: { color: '#10B981', labelBackgroundColor: '#059669' } },
      width: chartContainerRef.current.clientWidth,
      height: 400,
    })

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#00F5A0',
      downColor: '#FF5B7F',
      borderVisible: false,
      wickUpColor: '#10B981',
      wickDownColor: '#FF5B7F',
    })

    candlestickSeries.setData(chartData)
    chart.timeScale().fitContent()
    chartInstanceRef.current = chart

    const handleResize = () => {
      if (chartContainerRef.current) chart.applyOptions({ width: chartContainerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      try {
        chart.remove()
      } catch (e) {
        // wykres mógł już zostać usunięty (React StrictMode w dev)
      }
      if (chartInstanceRef.current === chart) chartInstanceRef.current = null
    }
  }, [chartData])

  return (
    <div style={{ minHeight: '100vh', position: 'relative', zIndex: 1 }}>
      {/* ===================== HEADER ===================== */}
      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 50,
          background: 'rgba(3,7,18,0.82)',
          backdropFilter: 'blur(18px)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <div style={{ maxWidth: '1180px', margin: '0 auto', padding: '14px 24px 0' }}>
          <div className="hl-brand" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '20px', flexWrap: 'wrap' }}>
            <BrandLockup />
            <div className="hl-badge hl-badge-accent hl-fade-in" style={{ animationDelay: '0.5s' }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-bright)', boxShadow: '0 0 8px var(--accent-bright)' }} />
              Gemini 3.6 Flash
            </div>
          </div>

          {/* Zakładki */}
          <nav
            className="hl-stagger"
            style={{ display: 'flex', gap: '2px', marginTop: '14px', overflowX: 'auto', paddingBottom: '2px' }}
          >
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => switchTab(t.key)}
                className={`hl-tab ${activeTab === t.key ? 'hl-tab-active' : ''}`}
              >
                <span style={{ marginRight: '7px', fontSize: '10px', opacity: 0.8, color: activeTab === t.key ? 'var(--accent-bright)' : 'inherit' }}>
                  {t.icon}
                </span>
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {/* ===================== TREŚĆ ===================== */}
      <main key={tabKey} className="hl-fade-up" style={{ maxWidth: '1180px', margin: '0 auto', padding: '28px 24px 60px' }}>
        {activeTab === 'analiza' && (
          <>
            <div className="hl-panel" style={{ padding: '22px', marginBottom: '22px' }}>
              <div style={{ display: 'flex', gap: '10px', marginBottom: '16px', flexWrap: 'wrap' }}>
                <input
                  value={ticker}
                  onChange={(e) => setTicker(e.target.value)}
                  placeholder="Ticker"
                  className="hl-input hl-num"
                  style={{ width: '140px', fontWeight: 600 }}
                />
                <input
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && !loading && analyzeStock()}
                  placeholder="O co chcesz zapytać?"
                  className="hl-input"
                  style={{ flex: 1, minWidth: '240px' }}
                />
                <button onClick={analyzeStock} disabled={loading} className="hl-btn hl-btn-primary">
                  {loading ? 'Analizuję…' : 'Analizuj ▸'}
                </button>
              </div>

              <div style={{ display: 'flex', gap: '7px', alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--text-dim)', fontSize: '11.5px', letterSpacing: '0.6px', textTransform: 'uppercase', fontWeight: 600 }}>
                  Horyzont
                </span>
                {HORIZONS.map((h) => (
                  <button
                    key={h.key}
                    onClick={() => setHorizon(h.key)}
                    className={`hl-btn hl-btn-sm ${horizon === h.key ? 'hl-tab-active' : ''}`}
                    style={horizon === h.key ? { borderColor: 'var(--accent)', color: 'var(--text)' } : undefined}
                  >
                    {h.label}
                  </button>
                ))}
              </div>
            </div>

            {error && (
              <div
                className="hl-fade-up"
                style={{ color: '#FF8FA8', background: 'rgba(255,91,127,0.08)', border: '1px solid rgba(255,91,127,0.3)', padding: '13px 17px', borderRadius: 'var(--radius-sm)', marginBottom: '20px' }}
              >
                {error}
              </div>
            )}

            {loading && chartData.length === 0 && (
              <div style={{ marginBottom: '22px' }}>
                <StepLoader
                  title={`Analizuję ${ticker}`}
                  steps={[
                    'Pobieram notowania z giełdy',
                    'Liczę trend i profil wolumenu',
                    'Sprawdzam nadchodzące wydarzenia',
                    'Gemini opracowuje werdykt',
                  ]}
                />
              </div>
            )}

            <div
              className="hl-panel"
              ref={chartContainerRef}
              style={{ width: '100%', height: '400px', marginBottom: '22px', overflow: 'hidden', background: '#0B1120' }}
            >
              {chartData.length === 0 && !loading && (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '10px', color: 'var(--text-dim)', textAlign: 'center', padding: '20px' }}>
                  <div style={{ fontSize: '30px', opacity: 0.35 }}>◈</div>
                  <div style={{ fontSize: '14px' }}>Wpisz ticker i naciśnij <strong style={{ color: 'var(--accent)' }}>Analizuj</strong></div>
                  <div style={{ fontSize: '12px', color: 'var(--text-dim)' }}>np. CDR.WA, PKN.WA, NVDA</div>
                </div>
              )}
            </div>

            {aiAnalysis && (
              <div className="hl-panel hl-panel-glow hl-fade-up" style={{ padding: '24px 26px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '14px' }}>
                  <span style={{ fontSize: '15px' }}>◆</span>
                  <h3 style={{ margin: 0, fontSize: '15px', background: 'var(--gradient-hossa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>
                    Werdykt HossaLab
                  </h3>
                </div>
                <MarkdownView>{aiAnalysis}</MarkdownView>
              </div>
            )}
          </>
        )}

        {activeTab === 'portfel' && <PortfolioTracker />}
        {activeTab === 'rekomendacje' && <PortfolioReport />}
        {activeTab === 'newsy' && <PortfolioNews />}
        {activeTab === 'dywidendy' && <DividendCalendar />}
        {activeTab === 'alerty' && <PriceAlerts />}
        {activeTab === 'sprzedaze' && <SalesHistory />}
        {activeTab === 'dane' && <BackupPanel />}
      </main>
    </div>
  )
}

export default App