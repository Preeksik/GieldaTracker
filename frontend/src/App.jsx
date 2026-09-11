// Zaktualizowany App.jsx - z poprawioną widocznością tekstu i działającymi zakładkami
import { useState, useEffect, useRef } from 'react'
import { createChart, CandlestickSeries } from 'lightweight-charts'
import PortfolioTracker from './PortfolioTracker'
import PortfolioReport from './PortfolioReport'
import PortfolioNews from './PortfolioNews'
import DividendCalendar from './DividendCalendar'
import PriceAlerts from './PriceAlerts'
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
        // wykres mógł już zostać usunięty (np. przez React StrictMode w dev) - ignorujemy
      }
      if (chartInstanceRef.current === chart) {
        chartInstanceRef.current = null
      }
    }
  }, [chartData])

  return (
    <div style={{ padding: '20px', fontFamily: 'system-ui', maxWidth: '900px', margin: '0 auto', color: '#fff' }}>
      <h1 style={{ color: '#fff', marginBottom: '60px' }}>📈 GPW + Google GeminiAI</h1>

      {/* Przełącznik zakładek */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
        <button
          onClick={() => setActiveTab('analiza')}
          style={{ padding: '8px 16px', background: activeTab === 'analiza' ? '#007BFF' : '#333', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
        >
          📊 Analiza
        </button>
        <button
          onClick={() => setActiveTab('portfel')}
          style={{ padding: '8px 16px', background: activeTab === 'portfel' ? '#007BFF' : '#333', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
        >
          💼 Portfel
        </button>
        <button
          onClick={() => setActiveTab('rekomendacje')}
          style={{ padding: '8px 16px', background: activeTab === 'rekomendacje' ? '#007BFF' : '#333', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
        >
          📊 Analiza i pytania
        </button>
        <button
          onClick={() => setActiveTab('newsy')}
          style={{ padding: '8px 16px', background: activeTab === 'newsy' ? '#007BFF' : '#333', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
        >
          🔥 Radar
        </button>
        <button
          onClick={() => setActiveTab('dywidendy')}
          style={{ padding: '8px 16px', background: activeTab === 'dywidendy' ? '#007BFF' : '#333', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
        >
          📅 Dywidendy
        </button>
        <button
          onClick={() => setActiveTab('alerty')}
          style={{ padding: '8px 16px', background: activeTab === 'alerty' ? '#007BFF' : '#333', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
        >
          🔔 Alerty
        </button>
      </div>

      {/* Zawartość zakładki "Analiza" - renderuje się TYLKO gdy activeTab === 'analiza' */}
      {activeTab === 'analiza' && (
        <>
          <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              style={{ padding: '10px', fontSize: '16px', borderRadius: '5px', border: '1px solid #ccc', color: '#fff', backgroundColor: '#333' }}
            />
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              style={{ flex: 1, padding: '10px', fontSize: '16px', borderRadius: '5px', border: '1px solid #ccc', color: '#fff', backgroundColor: '#333' }}
            />
            <button
              onClick={analyzeStock}
              disabled={loading}
              style={{ padding: '10px 20px', background: '#007BFF', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
            >
              {loading ? 'Myślę...' : 'Analizuj'}
            </button>
          </div>

          {error && <div style={{ color: 'red', marginBottom: '20px' }}>{error}</div>}

          <div ref={chartContainerRef} style={{ width: '100%', height: '400px', marginBottom: '20px', borderRadius: '8px', overflow: 'hidden', boxShadow: '0 4px 6px rgba(0,0,0,0.1)', backgroundColor: '#1E1E2F' }}>
            {chartData.length === 0 && !loading && (
              <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#2c2c2c', color: '#aaa', padding: '20px', textAlign: 'center' }}>
                Wpisz ticker (np. CDR.WA, PKN.WA) i kliknij "Analizuj".
              </div>
            )}
          </div>

          {aiAnalysis && (
            <div style={{ background: '#333', padding: '20px', borderRadius: '8px', borderLeft: '4px solid #007BFF', boxShadow: '0 2px 4px rgba(0,0,0,0.2)' }}>
              <h3 style={{ marginTop: 0, color: '#007BFF' }}>🤖 Werdykt DeepSeek:</h3>
              <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', margin: 0, color: '#eee' }}>{aiAnalysis}</p>
            </div>
          )}
        </>
      )}

      {/* Zawartość zakładki "Portfel" - renderuje się TYLKO gdy activeTab === 'portfel' */}
      {activeTab === 'portfel' && <PortfolioTracker />}

      {/* Zawartość zakładki "Rekomendacje" - renderuje się TYLKO gdy activeTab === 'rekomendacje' */}
      {activeTab === 'rekomendacje' && <PortfolioReport />}

      {/* Zawartość zakładki "Newsy" - renderuje się TYLKO gdy activeTab === 'newsy' */}
      {activeTab === 'newsy' && <PortfolioNews />}

      {/* Zawartość zakładki "Dywidendy" - renderuje się TYLKO gdy activeTab === 'dywidendy' */}
      {activeTab === 'dywidendy' && <DividendCalendar />}

      {/* Zawartość zakładki "Alerty" - renderuje się TYLKO gdy activeTab === 'alerty' */}
      {activeTab === 'alerty' && <PriceAlerts />}
    </div>
  )
}
export default App