// Zaktualizowany App.jsx - z brandingiem HossaLab i działającymi zakładkami
import { useState, useEffect, useRef } from 'react'
import { createChart, CandlestickSeries } from 'lightweight-charts'
import PortfolioTracker from './PortfolioTracker'
import PortfolioReport from './PortfolioReport'
import PortfolioNews from './PortfolioNews'
import DividendCalendar from './DividendCalendar'
import PriceAlerts from './PriceAlerts'
import SalesHistory from './SalesHistory'
import './App.css'

function App() {
  const [ticker, setTicker] = useState('CDR.WA')
  const [question, setQuestion] = useState('Jak oceniasz aktualny trend spółki?')
  const [chartData, setChartData] = useState([])
  const [aiAnalysis, setAiAnalysis] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('analiza')

  const chartContainerRef = useRef(null)
  const chartInstanceRef = useRef(null)

  const analyzeStock = async () => {
    setLoading(true)
    setError('')
    setAiAnalysis('')

    try {
      const response = await fetch('http://127.0.0.1:8000/api/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ticker: ticker,
          question: question,
          days: 30
        })
      })

      if (!response.ok) {
        throw new Error('Błąd pobierania danych z backendu.')
      }

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
      layout: { background: { type: 'solid', color: '#1E1E2F' }, textColor: '#DDD' },
      grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
      width: chartContainerRef.current.clientWidth,
      height: 400,
    })

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#4CAF50', downColor: '#FF5252', borderVisible: false, wickUpColor: '#4CAF50', wickDownColor: '#FF5252',
    })

    candlestickSeries.setData(chartData)
    chart.timeScale().fitContent()
    chartInstanceRef.current = chart

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      try {
        chart.remove()
      } catch (e) {
        // Ignorujemy błąd podwójnego czyszczenia wykresu w React StrictMode
      }
      if (chartInstanceRef.current === chart) {
        chartInstanceRef.current = null
      }
    }
  }, [chartData])

  return (
    <div style={{ padding: '20px', fontFamily: 'system-ui, -apple-system, sans-serif', maxWidth: '950px', margin: '0 auto', color: '#fff' }}>
      
      {/* Nagłówek HossaLab z logo */}
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '35px', paddingBottom: '15px', borderBottom: '1px solid #2d3748' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <img src="/Logo.svg" alt="HossaLab Logo" style={{ width: '46px', height: '46px' }} />
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <span style={{ fontWeight: 900, fontSize: '1.6rem', color: '#fff', letterSpacing: '0.5px', lineHeight: '1.1' }}>
              HOSSA<span style={{ color: '#10B981' }}>LAB</span>
            </span>
            <span style={{ fontSize: '0.75rem', color: '#94A3B8', letterSpacing: '2px', fontWeight: 600 }}>
              AI QUANT RESEARCH
            </span>
          </div>
        </div>

        <div style={{ fontSize: '0.85rem', color: '#64748B', background: '#111827', padding: '6px 12px', borderRadius: '6px', border: '1px solid #1F2937' }}>
          GPW & Global ETF Tracker
        </div>
      </header>

      {/* Przełącznik zakładek */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '25px', flexWrap: 'wrap' }}>
        <button
          onClick={() => setActiveTab('analiza')}
          style={{ padding: '8px 16px', background: activeTab === 'analiza' ? '#10B981' : '#1E293B', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
        >
          📊 Analiza
        </button>
        <button
          onClick={() => setActiveTab('portfel')}
          style={{ padding: '8px 16px', background: activeTab === 'portfel' ? '#10B981' : '#1E293B', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
        >
          💼 Portfel
        </button>
        <button
          onClick={() => setActiveTab('rekomendacje')}
          style={{ padding: '8px 16px', background: activeTab === 'rekomendacje' ? '#10B981' : '#1E293B', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
        >
          📈 Raport AI
        </button>
        <button
          onClick={() => setActiveTab('newsy')}
          style={{ padding: '8px 16px', background: activeTab === 'newsy' ? '#10B981' : '#1E293B', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
        >
          🔥 Radar
        </button>
        <button
          onClick={() => setActiveTab('dywidendy')}
          style={{ padding: '8px 16px', background: activeTab === 'dywidendy' ? '#10B981' : '#1E293B', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
        >
          📅 Dywidendy
        </button>
        <button
          onClick={() => setActiveTab('alerty')}
          style={{ padding: '8px 16px', background: activeTab === 'alerty' ? '#10B981' : '#1E293B', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
        >
          🔔 Alerty
        </button>
        <button
          onClick={() => setActiveTab('sprzedaze')}
          style={{ padding: '8px 16px', background: activeTab === 'sprzedaze' ? '#10B981' : '#1E293B', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
        >
          💰 Sprzedaże
        </button>
      </div>

      {/* Zawartość zakładki "Analiza" */}
      {activeTab === 'analiza' && (
        <>
          <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="np. CDR.WA"
              style={{ width: '130px', padding: '10px 14px', fontSize: '15px', borderRadius: '6px', border: '1px solid #334155', color: '#fff', backgroundColor: '#0F172A' }}
            />
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Zadaj pytanie analityczne..."
              style={{ flex: 1, padding: '10px 14px', fontSize: '15px', borderRadius: '6px', border: '1px solid #334155', color: '#fff', backgroundColor: '#0F172A' }}
            />
            <button
              onClick={analyzeStock}
              disabled={loading}
              style={{ padding: '10px 22px', background: loading ? '#047857' : '#10B981', color: '#04130C', fontWeight: 700, border: 'none', borderRadius: '6px', cursor: loading ? 'not-allowed' : 'pointer', transition: '0.2s' }}
            >
              {loading ? 'Analizuję...' : 'Analizuj'}
            </button>
          </div>

          {error && (
            <div style={{ color: '#F87171', background: '#450A0A', padding: '12px', borderRadius: '6px', marginBottom: '20px', border: '1px solid #991B1B' }}>
              {error}
            </div>
          )}

          <div ref={chartContainerRef} style={{ width: '100%', height: '400px', marginBottom: '20px', borderRadius: '8px', overflow: 'hidden', boxShadow: '0 4px 12px rgba(0,0,0,0.3)', backgroundColor: '#1E1E2F' }}>
            {chartData.length === 0 && !loading && (
              <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0F172A', color: '#64748B', padding: '20px', textAlign: 'center' }}>
                Wpisz ticker (np. CDR.WA, PKN.WA, PKO.WA) i kliknij "Analizuj".
              </div>
            )}
          </div>

          {aiAnalysis && (
            <div style={{ background: '#0F172A', padding: '22px', borderRadius: '8px', borderLeft: '4px solid #10B981', boxShadow: '0 4px 10px rgba(0,0,0,0.25)', border: '1px solid #1E293B', borderLeftWidth: '4px' }}>
              <h3 style={{ marginTop: 0, color: '#10B981', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '1.1rem' }}>
                ⚡ Werdykt HossaLab AI:
              </h3>
              <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', margin: 0, color: '#E2E8F0', fontSize: '0.95rem' }}>{aiAnalysis}</p>
            </div>
          )}
        </>
      )}

      {/* Pozostałe zakładki */}
      {activeTab === 'portfel' && <PortfolioTracker />}
      {activeTab === 'rekomendacje' && <PortfolioReport />}
      {activeTab === 'newsy' && <PortfolioNews />}
      {activeTab === 'dywidendy' && <DividendCalendar />}
      {activeTab === 'alerty' && <PriceAlerts />}
      {activeTab === 'sprzedaze' && <SalesHistory />}
    </div>
  )
}

export default App