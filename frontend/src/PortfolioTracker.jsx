import { useState, useEffect, Fragment } from 'react'
import PortfolioHistoryChart from './PortfolioHistoryChart'
import MarkdownView from './Markdownview'

const API_URL = 'http://127.0.0.1:8000'

const inputStyle = {
  padding: '10px',
  fontSize: '14px',
  borderRadius: '5px',
  border: '1px solid #ccc',
  color: '#fff',
  backgroundColor: '#333',
}

const horizonLabels = {
  krotki: 'Krótkoterminowo',
  sredni: 'Średnioterminowo',
  dlugi: 'Długoterminowo',
}

/** Grupuje płaską listę transakcji po tickerze i liczy zsumowane/uśrednione wartości nagłówka grupy. */
function groupByTicker(positions) {
  const groups = {}
  positions.forEach((pos) => {
    if (!groups[pos.ticker]) {
      groups[pos.ticker] = {
        ticker: pos.ticker,
        name: pos.name || pos.ticker,
        currency: pos.currency,
        quote_currency: pos.quote_currency,
        current_price: pos.current_price,
        lots: [],
        totalQuantity: 0,
        totalCost: 0,
        totalValue: 0,
        hasNulls: false,
      }
    }
    const g = groups[pos.ticker]
    g.lots.push(pos)
    g.totalQuantity += pos.quantity
    if (pos.cost !== null) g.totalCost += pos.cost
    if (pos.value !== null) {
      g.totalValue += pos.value
    } else {
      g.hasNulls = true
    }
  })

  return Object.values(groups).map((g) => {
    const weightedAvgBuyPrice =
      g.lots.reduce((sum, p) => sum + p.buy_price * p.quantity, 0) / g.totalQuantity
    const totalProfit = g.hasNulls ? null : g.totalValue - g.totalCost
    const totalProfitPct = totalProfit !== null && g.totalCost ? (totalProfit / g.totalCost) * 100 : null
    return { ...g, weightedAvgBuyPrice, totalProfit, totalProfitPct }
  })
}

function PortfolioTracker() {
  const [positions, setPositions] = useState([])
  const [summary, setSummary] = useState(null)
  const [accountsSummary, setAccountsSummary] = useState(null)
  const [accountFilter, setAccountFilter] = useState('wszystkie') // 'wszystkie' | 'zwykle' | 'ike' | 'ikze'
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [form, setForm] = useState({
    ticker: '',
    quantity: '',
    buy_price: '',
    currency: '', // puste = backend sam wykryje walutę po tickerze
    account: 'zwykle',
    buy_date: '',
    note: '',
  })

  // Które grupy tickerów są rozwinięte (pokazują poszczególne transakcje)
  const [expandedTickers, setExpandedTickers] = useState({})

  // Stan analizy "co z tym zrobić" dla POJEDYNCZEJ TRANSAKCJI (lotu)
  const [openAnalysisId, setOpenAnalysisId] = useState(null)
  const [horizon, setHorizon] = useState('sredni')
  const [customNote, setCustomNote] = useState('')
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [analysisResult, setAnalysisResult] = useState(null)
  const [followUps, setFollowUps] = useState([]) // [{question, answer}]
  const [followUpQuestion, setFollowUpQuestion] = useState('')
  const [followUpLoading, setFollowUpLoading] = useState(false)
  const [followUpError, setFollowUpError] = useState('')

  // Stan analizy dla CAŁEGO TICKERA (suma wszystkich transakcji tej spółki)
  const [openTickerAnalysisFor, setOpenTickerAnalysisFor] = useState(null)
  const [tickerHorizon, setTickerHorizon] = useState('sredni')
  const [tickerCustomNote, setTickerCustomNote] = useState('')
  const [tickerAnalysisLoading, setTickerAnalysisLoading] = useState(false)
  const [tickerAnalysisError, setTickerAnalysisError] = useState('')
  const [tickerAnalysisResult, setTickerAnalysisResult] = useState(null)
  const [tickerFollowUps, setTickerFollowUps] = useState([])
  const [tickerFollowUpQuestion, setTickerFollowUpQuestion] = useState('')
  const [tickerFollowUpLoading, setTickerFollowUpLoading] = useState(false)
  const [tickerFollowUpError, setTickerFollowUpError] = useState('')

  // Import z pliku CSV
  const [importLoading, setImportLoading] = useState(false)
  const [importResult, setImportResult] = useState(null)

  // Import pełnej historii z XTB (zakupy + sprzedaże)
  const [xtbLoading, setXtbLoading] = useState(false)
  const [xtbResult, setXtbResult] = useState(null)
  const [xtbError, setXtbError] = useState('')
  const [xtbAccount, setXtbAccount] = useState('zwykle')

  const handleXtbImport = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setXtbLoading(true)
    setXtbResult(null)
    setXtbError('')

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch(`${API_URL}/api/portfolio/import-xtb-history?account=${xtbAccount}`, {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się zaimportować historii XTB.')
      }
      setXtbResult(await res.json())
      fetchPortfolio()
    } catch (err) {
      setXtbError(err.message)
    } finally {
      setXtbLoading(false)
      e.target.value = ''
    }
  }

  const fetchPortfolio = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio`)
      if (!res.ok) throw new Error('Nie udało się pobrać portfela.')
      const data = await res.json()
      setPositions(data.positions)
      setSummary(data.summary)
      setAccountsSummary(data.accounts_summary)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchPortfolio()
  }, [])

  const handleChange = (e) => {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  const handleAdd = async (e) => {
    e.preventDefault()
    if (!form.ticker || !form.quantity || !form.buy_price || !form.buy_date) {
      setError('Uzupełnij ticker, ilość, cenę zakupu i datę.')
      return
    }
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: form.ticker,
          quantity: parseFloat(form.quantity),
          buy_price: parseFloat(form.buy_price),
          currency: form.currency,
          account: form.account,
          buy_date: form.buy_date,
          note: form.note,
        }),
      })
      if (!res.ok) throw new Error('Nie udało się dodać pozycji.')
      setForm({ ticker: '', quantity: '', buy_price: '', currency: '', account: 'zwykle', buy_date: '', note: '' })
      fetchPortfolio()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleCsvImport = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setImportLoading(true)
    setImportResult(null)
    setError('')

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch(`${API_URL}/api/portfolio/import-csv`, {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się zaimportować pliku CSV.')
      }
      const data = await res.json()
      setImportResult(data)
      fetchPortfolio()
    } catch (err) {
      setError(err.message)
    } finally {
      setImportLoading(false)
      e.target.value = ''
    }
  }

  const handleDelete = async (id) => {
    try {
      const res = await fetch(`${API_URL}/api/portfolio/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Nie udało się usunąć pozycji.')
      fetchPortfolio()
    } catch (err) {
      setError(err.message)
    }
  }

  const toggleExpanded = (ticker) => {
    setExpandedTickers({ ...expandedTickers, [ticker]: !expandedTickers[ticker] })
  }

  const handleAccountChange = async (id, newAccount) => {
    try {
      const res = await fetch(`${API_URL}/api/portfolio/${id}/account`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: newAccount }),
      })
      if (!res.ok) throw new Error('Nie udało się zmienić konta.')
      fetchPortfolio()
    } catch (err) {
      setError(err.message)
    }
  }

  // --- Sprzedaż (całości lub części) pojedynczej transakcji ---
  const [sellFormFor, setSellFormFor] = useState(null)
  const [sellForm, setSellForm] = useState({ quantity: '', sell_price: '', sell_date: '' })
  const [sellLoading, setSellLoading] = useState(false)
  const [sellError, setSellError] = useState('')

  const openSellForm = (pos) => {
    if (sellFormFor === pos.id) {
      setSellFormFor(null)
      return
    }
    setSellFormFor(pos.id)
    setSellForm({ quantity: String(pos.quantity), sell_price: '', sell_date: '' })
    setSellError('')
  }

  const submitSell = async (id) => {
    if (!sellForm.quantity || !sellForm.sell_price || !sellForm.sell_date) {
      setSellError('Uzupełnij ilość, cenę sprzedaży i datę.')
      return
    }
    setSellLoading(true)
    setSellError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/${id}/sell`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          quantity: parseFloat(sellForm.quantity),
          sell_price: parseFloat(sellForm.sell_price),
          sell_date: sellForm.sell_date,
        }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się zapisać sprzedaży.')
      }
      setSellFormFor(null)
      fetchPortfolio()
    } catch (err) {
      setSellError(err.message)
    } finally {
      setSellLoading(false)
    }
  }

  // --- Analiza pojedynczej transakcji (lotu) ---
  const openAnalysis = (id) => {
    if (openAnalysisId === id) {
      setOpenAnalysisId(null)
      return
    }
    setOpenAnalysisId(id)
    setAnalysisResult(null)
    setAnalysisError('')
    setHorizon('sredni')
    setCustomNote('')
    setFollowUps([])
    setFollowUpQuestion('')
    setFollowUpError('')
  }

  const runAnalysis = async (id) => {
    setAnalysisLoading(true)
    setAnalysisError('')
    setAnalysisResult(null)
    setFollowUps([])
    try {
      const res = await fetch(`${API_URL}/api/portfolio/${id}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ horizon, custom_note: customNote }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się wygenerować analizy.')
      }
      const data = await res.json()
      setAnalysisResult(data)
    } catch (err) {
      setAnalysisError(err.message)
    } finally {
      setAnalysisLoading(false)
    }
  }

  const askFollowUp = async (id) => {
    if (!followUpQuestion.trim()) return
    setFollowUpLoading(true)
    setFollowUpError('')
    const contextText =
      analysisResult.analysis +
      followUps.map((f) => `\n\nPytanie: ${f.question}\nOdpowiedź: ${f.answer}`).join('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/${id}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          horizon,
          previous_analysis: contextText,
          follow_up_question: followUpQuestion,
        }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się uzyskać odpowiedzi.')
      }
      const data = await res.json()
      setFollowUps([...followUps, { question: followUpQuestion, answer: data.analysis }])
      setFollowUpQuestion('')
    } catch (err) {
      setFollowUpError(err.message)
    } finally {
      setFollowUpLoading(false)
    }
  }

  // --- Analiza całego tickera (suma transakcji) ---
  const openTickerAnalysis = (ticker) => {
    if (openTickerAnalysisFor === ticker) {
      setOpenTickerAnalysisFor(null)
      return
    }
    setOpenTickerAnalysisFor(ticker)
    setTickerAnalysisResult(null)
    setTickerAnalysisError('')
    setTickerHorizon('sredni')
    setTickerCustomNote('')
    setTickerFollowUps([])
    setTickerFollowUpQuestion('')
    setTickerFollowUpError('')
  }

  const runTickerAnalysis = async (ticker) => {
    setTickerAnalysisLoading(true)
    setTickerAnalysisError('')
    setTickerAnalysisResult(null)
    setTickerFollowUps([])
    try {
      const res = await fetch(`${API_URL}/api/portfolio/ticker/${ticker}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ horizon: tickerHorizon, custom_note: tickerCustomNote }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się wygenerować analizy.')
      }
      const data = await res.json()
      setTickerAnalysisResult(data)
    } catch (err) {
      setTickerAnalysisError(err.message)
    } finally {
      setTickerAnalysisLoading(false)
    }
  }

  const askTickerFollowUp = async (ticker) => {
    if (!tickerFollowUpQuestion.trim()) return
    setTickerFollowUpLoading(true)
    setTickerFollowUpError('')
    const contextText =
      tickerAnalysisResult.analysis +
      tickerFollowUps.map((f) => `\n\nPytanie: ${f.question}\nOdpowiedź: ${f.answer}`).join('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/ticker/${ticker}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          horizon: tickerHorizon,
          previous_analysis: contextText,
          follow_up_question: tickerFollowUpQuestion,
        }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się uzyskać odpowiedzi.')
      }
      const data = await res.json()
      setTickerFollowUps([...tickerFollowUps, { question: tickerFollowUpQuestion, answer: data.analysis }])
      setTickerFollowUpQuestion('')
    } catch (err) {
      setTickerFollowUpError(err.message)
    } finally {
      setTickerFollowUpLoading(false)
    }
  }

  const filteredPositions =
    accountFilter === 'wszystkie' ? positions : positions.filter((p) => p.account === accountFilter)
  const grouped = groupByTicker(filteredPositions)

  const accountLabels = { zwykle: 'Zwykłe', ike: 'IKE', ikze: 'IKZE' }

  return (
    <div style={{ color: '#fff' }}>
      <PortfolioHistoryChart />

      <h2 style={{ marginBottom: '15px' }}>💼 Mój Portfel</h2>

      <form
        onSubmit={handleAdd}
        style={{ display: 'flex', gap: '10px', marginBottom: '20px', flexWrap: 'wrap' }}
      >
        <input
          name="ticker"
          placeholder="Ticker (np. CDR.WA)"
          value={form.ticker}
          onChange={handleChange}
          style={{ ...inputStyle, width: '140px' }}
        />
        <input
          name="quantity"
          placeholder="Ilość"
          type="number"
          step="any"
          value={form.quantity}
          onChange={handleChange}
          style={{ ...inputStyle, width: '100px' }}
        />
        <input
          name="buy_price"
          placeholder="Cena zakupu"
          type="number"
          step="any"
          value={form.buy_price}
          onChange={handleChange}
          style={{ ...inputStyle, width: '120px' }}
        />
        <select
          name="currency"
          value={form.currency}
          onChange={handleChange}
          style={{ ...inputStyle, width: '100px' }}
        >
          <option value="">Auto</option>
          <option value="PLN">PLN</option>
          <option value="USD">USD</option>
          <option value="EUR">EUR</option>
          <option value="GBP">GBP</option>
        </select>
        <select
          name="account"
          value={form.account}
          onChange={handleChange}
          style={{ ...inputStyle, width: '110px' }}
        >
          <option value="zwykle">Zwykłe</option>
          <option value="ike">IKE</option>
          <option value="ikze">IKZE</option>
        </select>
        <input
          name="buy_date"
          type="date"
          value={form.buy_date}
          onChange={handleChange}
          style={{ ...inputStyle, width: '150px' }}
        />
        <input
          name="note"
          placeholder="Notatka (opcjonalnie)"
          value={form.note}
          onChange={handleChange}
          style={{ ...inputStyle, flex: 1, minWidth: '150px' }}
        />
        <button
          type="submit"
          style={{
            padding: '10px 20px',
            background: '#007BFF',
            color: 'white',
            border: 'none',
            borderRadius: '5px',
            cursor: 'pointer',
          }}
        >
          Dodaj
        </button>
      </form>

      <div style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          <label
            style={{
              display: 'inline-block',
              padding: '10px 20px',
              background: '#444',
              color: 'white',
              borderRadius: '5px',
              cursor: importLoading ? 'not-allowed' : 'pointer',
            }}
          >
            {importLoading ? 'Importuję...' : '📁 Importuj z CSV'}
            <input
              type="file"
              accept=".csv"
              onChange={handleCsvImport}
              disabled={importLoading}
              style={{ display: 'none' }}
            />
          </label>

          <label
            style={{
              display: 'inline-block',
              padding: '10px 20px',
              background: '#1565C0',
              color: 'white',
              borderRadius: '5px',
              cursor: xtbLoading ? 'not-allowed' : 'pointer',
            }}
          >
            {xtbLoading ? 'Importuję historię...' : '📊 Importuj historię XTB'}
            <input
              type="file"
              accept=".csv"
              onChange={handleXtbImport}
              disabled={xtbLoading}
              style={{ display: 'none' }}
            />
          </label>

          <select
            value={xtbAccount}
            onChange={(e) => setXtbAccount(e.target.value)}
            style={{ ...inputStyle, width: '110px' }}
            title="Konto, do którego trafią importowane transakcje XTB"
          >
            <option value="zwykle">Zwykłe</option>
            <option value="ike">IKE</option>
            <option value="ikze">IKZE</option>
          </select>
        </div>

        <div style={{ color: '#777', fontSize: '12px', marginTop: '8px' }}>
          <strong>Zwykły CSV</strong>: tylko otwarte pozycje (ticker, ilość, cena, data). <strong>Historia XTB</strong>:
          pełen eksport z platformy — otwarte pozycje trafią do portfela, zamknięte do zakładki Sprzedaże.
        </div>

        {importResult && (
          <div style={{ marginTop: '10px', color: importResult.errors.length > 0 ? '#FFA726' : '#4CAF50' }}>
            ✅ Zaimportowano {importResult.added} pozycji.
            {importResult.errors.length > 0 && (
              <div style={{ color: '#FF5252', marginTop: '5px' }}>
                Błędy w {importResult.errors.length} wierszach:
                <ul style={{ margin: '5px 0 0 20px', padding: 0 }}>
                  {importResult.errors.map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {xtbResult && (
          <div style={{ marginTop: '10px', color: '#4CAF50' }}>
            ✅ Zaimportowano z XTB: {xtbResult.imported_open_positions} otwartych pozycji,{' '}
            {xtbResult.imported_closed_positions} zamkniętych (→ zakładka Sprzedaże).
            <div style={{ color: '#888', fontSize: '12px' }}>Tickery: {xtbResult.tickers.join(', ')}</div>
            {xtbResult.errors.length > 0 && (
              <div style={{ color: '#FFA726', marginTop: '5px', fontSize: '12px' }}>
                Pominięto {xtbResult.errors.length} wierszy: {xtbResult.errors.slice(0, 3).join(' · ')}
              </div>
            )}
          </div>
        )}

        {xtbError && <div style={{ color: '#FF5252', marginTop: '10px' }}>{xtbError}</div>}
      </div>

      {error && <div style={{ color: '#FF5252', marginBottom: '15px' }}>{error}</div>}
      {loading && <div style={{ color: '#aaa', marginBottom: '15px' }}>Ładowanie...</div>}

      {summary && (
        <div
          style={{
            display: 'flex',
            gap: '25px',
            marginBottom: '15px',
            background: '#333',
            padding: '15px',
            borderRadius: '8px',
            flexWrap: 'wrap',
          }}
        >
          <div>
            Koszt: <strong>{summary.total_cost.toFixed(2)} PLN</strong>
          </div>
          <div>
            Wartość: <strong>{summary.total_value.toFixed(2)} PLN</strong>
          </div>
          <div style={{ color: summary.total_profit >= 0 ? '#4CAF50' : '#FF5252' }}>
            Zysk/Strata:{' '}
            <strong>
              {summary.total_profit.toFixed(2)} PLN ({summary.total_profit_pct.toFixed(2)}%)
            </strong>
          </div>
        </div>
      )}

      {/* Filtr kont + rozbicie z podatkiem Belki */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '15px', flexWrap: 'wrap' }}>
        {['wszystkie', 'zwykle', 'ike', 'ikze'].map((acc) => (
          <button
            key={acc}
            onClick={() => setAccountFilter(acc)}
            style={{
              padding: '8px 16px',
              background: accountFilter === acc ? '#007BFF' : '#333',
              color: 'white',
              border: 'none',
              borderRadius: '5px',
              cursor: 'pointer',
            }}
          >
            {acc === 'wszystkie' ? 'Wszystkie konta' : accountLabels[acc]}
          </button>
        ))}
      </div>

      {accountsSummary && (
        <div style={{ display: 'flex', gap: '15px', marginBottom: '20px', flexWrap: 'wrap' }}>
          {['zwykle', 'ike', 'ikze'].map((acc) => {
            const a = accountsSummary[acc]
            if (!a || a.total_cost === 0) return null
            return (
              <div
                key={acc}
                style={{
                  background: '#2a2a2a',
                  padding: '12px 16px',
                  borderRadius: '8px',
                  fontSize: '13px',
                  minWidth: '220px',
                }}
              >
                <div style={{ fontWeight: 'bold', marginBottom: '6px' }}>{accountLabels[acc]}</div>
                <div>Wartość: {a.total_value.toFixed(2)} PLN</div>
                <div style={{ color: a.total_profit >= 0 ? '#4CAF50' : '#FF5252' }}>
                  Zysk brutto: {a.total_profit.toFixed(2)} PLN
                </div>
                {acc === 'zwykle' ? (
                  <>
                    <div style={{ color: '#FFA726' }}>
                      Szac. podatek Belki (19%): {a.total_tax_estimate.toFixed(2)} PLN
                    </div>
                    <div style={{ color: a.total_profit_after_tax >= 0 ? '#4CAF50' : '#FF5252' }}>
                      Zysk po podatku: {a.total_profit_after_tax.toFixed(2)} PLN
                    </div>
                  </>
                ) : (
                  <div style={{ color: '#4CAF50', fontSize: '12px' }}>✓ zwolnione z podatku Belki</div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {grouped.length === 0 && !loading ? (
        <div style={{ color: '#aaa' }}>Portfel jest pusty — dodaj pierwszą pozycję powyżej.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #555', textAlign: 'left' }}>
              <th style={{ padding: '8px' }}>Spółka</th>
              <th style={{ padding: '8px' }}>Ilość</th>
              <th style={{ padding: '8px' }}>Śr. cena zakupu</th>
              <th style={{ padding: '8px' }}>Cena aktualna</th>
              <th style={{ padding: '8px' }}>Wartość</th>
              <th style={{ padding: '8px' }}>Zysk/Strata</th>
              <th style={{ padding: '8px' }}></th>
              <th style={{ padding: '8px' }}></th>
              <th style={{ padding: '8px' }}></th>
            </tr>
          </thead>
          <tbody>
            {grouped.map((g) => {
              const isExpanded = !!expandedTickers[g.ticker]
              const isMulti = g.lots.length > 1

              return (
                <Fragment key={g.ticker}>
                  {/* --- Wiersz nagłówkowy grupy (jedna spółka, zsumowane wartości) --- */}
                  <tr
                    style={{
                      borderBottom: isExpanded || openTickerAnalysisFor === g.ticker ? 'none' : '1px solid #444',
                      background: isMulti ? '#2a2a2a' : 'transparent',
                    }}
                  >
                    <td style={{ padding: '8px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <button
                          onClick={() => toggleExpanded(g.ticker)}
                          style={{
                            background: 'none',
                            border: 'none',
                            color: '#aaa',
                            cursor: 'pointer',
                            fontSize: '12px',
                            padding: 0,
                          }}
                          title={isExpanded ? 'Zwiń szczegóły transakcji' : 'Pokaż szczegóły transakcji (konto, usuwanie)'}
                        >
                          {isExpanded ? '▼' : '▶'}
                        </button>
                        <div>
                          <div style={{ fontWeight: 'bold' }}>{g.name}</div>
                          <div style={{ fontSize: '12px', color: '#999' }}>
                            {g.ticker}
                            {isMulti && ` · ${g.lots.length} transakcje`}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td style={{ padding: '8px' }}>{g.totalQuantity}</td>
                    <td style={{ padding: '8px' }}>
                      {g.weightedAvgBuyPrice.toFixed(2)} {g.currency || 'PLN'}
                      {isMulti && <span style={{ color: '#777', fontSize: '11px' }}> (śr. ważona)</span>}
                    </td>
                    <td style={{ padding: '8px' }}>
                      {g.current_price !== null
                        ? `${g.current_price} ${g.quote_currency || g.currency || 'PLN'}`
                        : '—'}
                    </td>
                    <td style={{ padding: '8px' }}>
                      {!g.hasNulls ? `${g.totalValue.toFixed(2)} PLN` : '—'}
                    </td>
                    <td
                      style={{
                        padding: '8px',
                        color: g.totalProfit >= 0 ? '#4CAF50' : '#FF5252',
                      }}
                    >
                      {g.totalProfit !== null
                        ? `${g.totalProfit.toFixed(2)} PLN (${g.totalProfitPct.toFixed(2)}%)`
                        : '—'}
                    </td>
                    <td style={{ padding: '8px' }}>
                      <button
                        onClick={() => openTickerAnalysis(g.ticker)}
                        style={{
                          background: openTickerAnalysisFor === g.ticker ? '#007BFF' : '#444',
                          color: 'white',
                          border: 'none',
                          borderRadius: '5px',
                          padding: '5px 10px',
                          cursor: 'pointer',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        🔍 {isMulti ? 'Analiza całej pozycji' : 'Co z tym?'}
                      </button>
                    </td>
                    <td style={{ padding: '8px' }}></td>
                    <td style={{ padding: '8px' }}></td>
                  </tr>

                  {/* --- Panel analizy dla całego tickera (suma transakcji) --- */}
                  {openTickerAnalysisFor === g.ticker && (
                    <tr style={{ borderBottom: '1px solid #444' }}>
                      <td colSpan={9} style={{ padding: '15px', background: '#242424' }}>
                        <div style={{ marginBottom: '12px' }}>
                          <strong>Horyzont analizy dla całej pozycji {g.ticker}:</strong>
                          <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap' }}>
                            {Object.entries(horizonLabels).map(([key, label]) => (
                              <button
                                key={key}
                                onClick={() => setTickerHorizon(key)}
                                style={{
                                  padding: '6px 14px',
                                  background: tickerHorizon === key ? '#007BFF' : '#444',
                                  color: 'white',
                                  border: 'none',
                                  borderRadius: '5px',
                                  cursor: 'pointer',
                                }}
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                        </div>

                        <input
                          placeholder='Dodatkowy kontekst (opcjonalnie), np. "rozważam czasowe wyjście pod konferencję X"'
                          value={tickerCustomNote}
                          onChange={(e) => setTickerCustomNote(e.target.value)}
                          style={{ ...inputStyle, width: '100%', marginBottom: '12px', boxSizing: 'border-box' }}
                        />

                        <button
                          onClick={() => runTickerAnalysis(g.ticker)}
                          disabled={tickerAnalysisLoading}
                          style={{
                            padding: '10px 20px',
                            background: '#007BFF',
                            color: 'white',
                            border: 'none',
                            borderRadius: '5px',
                            cursor: tickerAnalysisLoading ? 'not-allowed' : 'pointer',
                            marginBottom: '12px',
                          }}
                        >
                          {tickerAnalysisLoading ? 'Analizuję... (do minuty)' : 'Generuj analizę'}
                        </button>

                        {tickerAnalysisError && (
                          <div style={{ color: '#FF5252', marginBottom: '12px' }}>{tickerAnalysisError}</div>
                        )}

                        {tickerAnalysisResult && (
                          <>
                            <div
                              style={{
                                background: '#333',
                                padding: '15px',
                                borderRadius: '8px',
                                borderLeft: '4px solid #007BFF',
                                marginBottom: '12px',
                              }}
                            >
                              <MarkdownView>{tickerAnalysisResult.analysis}</MarkdownView>
                            </div>

                            {tickerFollowUps.map((f, idx) => (
                              <div key={idx} style={{ marginBottom: '12px' }}>
                                <div style={{ color: '#007BFF', fontWeight: 'bold', marginBottom: '4px' }}>
                                  ❓ {f.question}
                                </div>
                                <div
                                  style={{
                                    background: '#2f2f2f',
                                    padding: '12px',
                                    borderRadius: '8px',
                                    borderLeft: '4px solid #555',
                                  }}
                                >
                                  <MarkdownView>{f.answer}</MarkdownView>
                                </div>
                              </div>
                            ))}

                            <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
                              <input
                                placeholder='Pytanie uzupełniające, np. "czy dokupić za nowe 5k?"'
                                value={tickerFollowUpQuestion}
                                onChange={(e) => setTickerFollowUpQuestion(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && askTickerFollowUp(g.ticker)}
                                style={{ ...inputStyle, flex: 1, boxSizing: 'border-box' }}
                              />
                              <button
                                onClick={() => askTickerFollowUp(g.ticker)}
                                disabled={tickerFollowUpLoading || !tickerFollowUpQuestion.trim()}
                                style={{
                                  padding: '10px 18px',
                                  background: '#007BFF',
                                  color: 'white',
                                  border: 'none',
                                  borderRadius: '5px',
                                  cursor: tickerFollowUpLoading ? 'not-allowed' : 'pointer',
                                  whiteSpace: 'nowrap',
                                }}
                              >
                                {tickerFollowUpLoading ? 'Pytam...' : 'Zapytaj'}
                              </button>
                            </div>
                            {tickerFollowUpError && (
                              <div style={{ color: '#FF5252', marginTop: '8px' }}>{tickerFollowUpError}</div>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  )}

                  {/* --- Rozwinięte poszczególne transakcje (loty) --- */}
                  {isExpanded &&
                    g.lots.map((pos) => (
                      <Fragment key={pos.id}>
                        <tr style={{ borderBottom: openAnalysisId === pos.id ? 'none' : '1px solid #3a3a3a', background: '#1c1c1c' }}>
                          <td style={{ padding: '8px 8px 8px 30px' }}>
                            <div style={{ fontSize: '13px', color: '#ccc' }}>{pos.buy_date}</div>
                            {pos.note && (
                              <div style={{ fontSize: '12px', color: '#888', fontStyle: 'italic', marginTop: '2px' }}>
                                📝 {pos.note}
                              </div>
                            )}
                            <select
                              value={pos.account || 'zwykle'}
                              onChange={(e) => handleAccountChange(pos.id, e.target.value)}
                              style={{
                                marginTop: '4px',
                                background: '#333',
                                color: '#ccc',
                                border: '1px solid #555',
                                borderRadius: '4px',
                                fontSize: '11px',
                                padding: '2px 4px',
                              }}
                            >
                              <option value="zwykle">Zwykłe</option>
                              <option value="ike">IKE</option>
                              <option value="ikze">IKZE</option>
                            </select>
                          </td>
                          <td style={{ padding: '8px' }}>{pos.quantity}</td>
                          <td style={{ padding: '8px' }}>{pos.buy_price} {pos.currency || 'PLN'}</td>
                          <td style={{ padding: '8px' }}>
                            {pos.current_price !== null
                              ? `${pos.current_price} ${pos.quote_currency || pos.currency || 'PLN'}`
                              : '—'}
                          </td>
                          <td style={{ padding: '8px' }}>{pos.value !== null ? `${pos.value} PLN` : '—'}</td>
                          <td style={{ padding: '8px', color: pos.profit >= 0 ? '#4CAF50' : '#FF5252' }}>
                            {pos.profit !== null ? `${pos.profit} PLN (${pos.profit_pct}%)` : '—'}
                          </td>
                          <td style={{ padding: '8px' }}>
                            <button
                              onClick={() => openAnalysis(pos.id)}
                              style={{
                                background: openAnalysisId === pos.id ? '#007BFF' : '#444',
                                color: 'white',
                                border: 'none',
                                borderRadius: '5px',
                                padding: '5px 10px',
                                cursor: 'pointer',
                                whiteSpace: 'nowrap',
                                fontSize: '12px',
                              }}
                            >
                              🔍 Co z tym?
                            </button>
                          </td>
                          <td style={{ padding: '8px' }}>
                            <button
                              onClick={() => openSellForm(pos)}
                              style={{
                                background: sellFormFor === pos.id ? '#007BFF' : '#2E7D32',
                                color: 'white',
                                border: 'none',
                                borderRadius: '5px',
                                padding: '5px 10px',
                                cursor: 'pointer',
                                fontSize: '12px',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              💰 Sprzedaj
                            </button>
                          </td>
                          <td style={{ padding: '8px' }}>
                            <button
                              onClick={() => handleDelete(pos.id)}
                              style={{
                                background: '#FF5252',
                                color: 'white',
                                border: 'none',
                                borderRadius: '5px',
                                padding: '5px 10px',
                                cursor: 'pointer',
                                fontSize: '12px',
                              }}
                            >
                              Usuń
                            </button>
                          </td>
                        </tr>

                        {sellFormFor === pos.id && (
                          <tr style={{ borderBottom: '1px solid #3a3a3a' }}>
                            <td colSpan={9} style={{ padding: '15px', background: '#1f2e1f' }}>
                              <strong>Sprzedaż {pos.ticker} (masz {pos.quantity} szt., kupione po {pos.buy_price} {pos.currency}):</strong>
                              <div style={{ display: 'flex', gap: '10px', marginTop: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
                                <input
                                  placeholder="Ilość"
                                  type="number"
                                  step="any"
                                  value={sellForm.quantity}
                                  onChange={(e) => setSellForm({ ...sellForm, quantity: e.target.value })}
                                  style={{ ...inputStyle, width: '100px' }}
                                />
                                <input
                                  placeholder={`Cena sprzedaży (${pos.currency})`}
                                  type="number"
                                  step="any"
                                  value={sellForm.sell_price}
                                  onChange={(e) => setSellForm({ ...sellForm, sell_price: e.target.value })}
                                  style={{ ...inputStyle, width: '160px' }}
                                />
                                <input
                                  type="date"
                                  value={sellForm.sell_date}
                                  onChange={(e) => setSellForm({ ...sellForm, sell_date: e.target.value })}
                                  style={{ ...inputStyle, width: '150px' }}
                                />
                                <button
                                  onClick={() => submitSell(pos.id)}
                                  disabled={sellLoading}
                                  style={{
                                    padding: '10px 20px',
                                    background: '#2E7D32',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '5px',
                                    cursor: sellLoading ? 'not-allowed' : 'pointer',
                                  }}
                                >
                                  {sellLoading ? 'Zapisuję...' : 'Potwierdź sprzedaż'}
                                </button>
                              </div>
                              {sellError && <div style={{ color: '#FF5252', marginTop: '10px' }}>{sellError}</div>}
                              <p style={{ color: '#888', fontSize: '11px', marginTop: '8px' }}>
                                Sprzedaż mniejszej ilości niż posiadasz zmniejszy tę pozycję (częściowe zamknięcie).
                                Realny zysk/strata i podatek zobaczysz w zakładce "Sprzedaże".
                              </p>
                            </td>
                          </tr>
                        )}

                        {openAnalysisId === pos.id && (
                          <tr style={{ borderBottom: '1px solid #3a3a3a' }}>
                            <td colSpan={9} style={{ padding: '15px', background: '#2a2a2a' }}>
                              <div style={{ marginBottom: '12px' }}>
                                <strong>Horyzont analizy tej transakcji ({pos.ticker}, {pos.buy_date}):</strong>
                                <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap' }}>
                                  {Object.entries(horizonLabels).map(([key, label]) => (
                                    <button
                                      key={key}
                                      onClick={() => setHorizon(key)}
                                      style={{
                                        padding: '6px 14px',
                                        background: horizon === key ? '#007BFF' : '#444',
                                        color: 'white',
                                        border: 'none',
                                        borderRadius: '5px',
                                        cursor: 'pointer',
                                      }}
                                    >
                                      {label}
                                    </button>
                                  ))}
                                </div>
                              </div>

                              <input
                                placeholder='Dodatkowy kontekst (opcjonalnie)'
                                value={customNote}
                                onChange={(e) => setCustomNote(e.target.value)}
                                style={{ ...inputStyle, width: '100%', marginBottom: '12px', boxSizing: 'border-box' }}
                              />

                              <button
                                onClick={() => runAnalysis(pos.id)}
                                disabled={analysisLoading}
                                style={{
                                  padding: '10px 20px',
                                  background: '#007BFF',
                                  color: 'white',
                                  border: 'none',
                                  borderRadius: '5px',
                                  cursor: analysisLoading ? 'not-allowed' : 'pointer',
                                  marginBottom: '12px',
                                }}
                              >
                                {analysisLoading ? 'Analizuję... (do minuty)' : 'Generuj analizę'}
                              </button>

                              {analysisError && (
                                <div style={{ color: '#FF5252', marginBottom: '12px' }}>{analysisError}</div>
                              )}

                              {analysisResult && (
                                <>
                                  <div
                                    style={{
                                      background: '#333',
                                      padding: '15px',
                                      borderRadius: '8px',
                                      borderLeft: '4px solid #007BFF',
                                      marginBottom: '12px',
                                    }}
                                  >
                                    <MarkdownView>{analysisResult.analysis}</MarkdownView>
                                  </div>

                                  {followUps.map((f, idx) => (
                                    <div key={idx} style={{ marginBottom: '12px' }}>
                                      <div style={{ color: '#007BFF', fontWeight: 'bold', marginBottom: '4px' }}>
                                        ❓ {f.question}
                                      </div>
                                      <div
                                        style={{
                                          background: '#2f2f2f',
                                          padding: '12px',
                                          borderRadius: '8px',
                                          borderLeft: '4px solid #555',
                                        }}
                                      >
                                        <MarkdownView>{f.answer}</MarkdownView>
                                      </div>
                                    </div>
                                  ))}

                                  <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
                                    <input
                                      placeholder='Pytanie uzupełniające'
                                      value={followUpQuestion}
                                      onChange={(e) => setFollowUpQuestion(e.target.value)}
                                      onKeyDown={(e) => e.key === 'Enter' && askFollowUp(pos.id)}
                                      style={{ ...inputStyle, flex: 1, boxSizing: 'border-box' }}
                                    />
                                    <button
                                      onClick={() => askFollowUp(pos.id)}
                                      disabled={followUpLoading || !followUpQuestion.trim()}
                                      style={{
                                        padding: '10px 18px',
                                        background: '#007BFF',
                                        color: 'white',
                                        border: 'none',
                                        borderRadius: '5px',
                                        cursor: followUpLoading ? 'not-allowed' : 'pointer',
                                        whiteSpace: 'nowrap',
                                      }}
                                    >
                                      {followUpLoading ? 'Pytam...' : 'Zapytaj'}
                                    </button>
                                  </div>
                                  {followUpError && (
                                    <div style={{ color: '#FF5252', marginTop: '8px' }}>{followUpError}</div>
                                  )}
                                </>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default PortfolioTracker