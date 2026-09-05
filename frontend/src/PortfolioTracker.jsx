import { useState, useEffect, Fragment } from 'react'

const API_URL = 'http://127.0.0.1:8000'

const inputStyle = {
  padding: '10px',
  fontSize: '14px',
  borderRadius: '5px',
  border: '1px solid #ccc',
  color: '#fff',
  backgroundColor: '#333',
}

function PortfolioTracker() {
  const [positions, setPositions] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [form, setForm] = useState({
    ticker: '',
    quantity: '',
    buy_price: '',
    buy_date: '',
    note: '',
  })

  // Stan analizy "co z tym zrobić" dla pojedynczej pozycji
  const [openAnalysisId, setOpenAnalysisId] = useState(null)
  const [horizon, setHorizon] = useState('sredni')
  const [customNote, setCustomNote] = useState('')
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [analysisResult, setAnalysisResult] = useState(null)

  // Wątek pytań uzupełniających pod główną analizą
  const [followUps, setFollowUps] = useState([]) // [{question, answer}]
  const [followUpQuestion, setFollowUpQuestion] = useState('')
  const [followUpLoading, setFollowUpLoading] = useState(false)
  const [followUpError, setFollowUpError] = useState('')

  const fetchPortfolio = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio`)
      if (!res.ok) throw new Error('Nie udało się pobrać portfela.')
      const data = await res.json()
      setPositions(data.positions)
      setSummary(data.summary)
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
          buy_date: form.buy_date,
          note: form.note,
        }),
      })
      if (!res.ok) throw new Error('Nie udało się dodać pozycji.')
      setForm({ ticker: '', quantity: '', buy_price: '', buy_date: '', note: '' })
      fetchPortfolio()
    } catch (err) {
      setError(err.message)
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

    // Kontekst dla AI: główna analiza + wszystkie dotychczasowe pytania/odpowiedzi w wątku
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

  const horizonLabels = {
    krotki: 'Krótkoterminowo',
    sredni: 'Średnioterminowo',
    dlugi: 'Długoterminowo',
  }

  return (
    <div style={{ color: '#fff' }}>
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

      {error && <div style={{ color: '#FF5252', marginBottom: '15px' }}>{error}</div>}
      {loading && <div style={{ color: '#aaa', marginBottom: '15px' }}>Ładowanie...</div>}

      {summary && (
        <div
          style={{
            display: 'flex',
            gap: '25px',
            marginBottom: '20px',
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

      {positions.length === 0 && !loading ? (
        <div style={{ color: '#aaa' }}>Portfel jest pusty — dodaj pierwszą pozycję powyżej.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #555', textAlign: 'left' }}>
              <th style={{ padding: '8px' }}>Spółka</th>
              <th style={{ padding: '8px' }}>Ilość</th>
              <th style={{ padding: '8px' }}>Cena zakupu</th>
              <th style={{ padding: '8px' }}>Cena aktualna</th>
              <th style={{ padding: '8px' }}>Wartość</th>
              <th style={{ padding: '8px' }}>Zysk/Strata</th>
              <th style={{ padding: '8px' }}>Data zakupu</th>
              <th style={{ padding: '8px' }}></th>
              <th style={{ padding: '8px' }}></th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos) => (
              <Fragment key={pos.id}>
              <tr style={{ borderBottom: openAnalysisId === pos.id ? 'none' : '1px solid #444' }}>
                <td style={{ padding: '8px' }}>
                  <div style={{ fontWeight: 'bold' }}>{pos.name || pos.ticker}</div>
                  <div style={{ fontSize: '12px', color: '#999' }}>{pos.ticker}</div>
                  {pos.note && (
                    <div style={{ fontSize: '12px', color: '#888', fontStyle: 'italic', marginTop: '2px' }}>
                      📝 {pos.note}
                    </div>
                  )}
                </td>
                <td style={{ padding: '8px' }}>{pos.quantity}</td>
                <td style={{ padding: '8px' }}>{pos.buy_price} PLN</td>
                <td style={{ padding: '8px' }}>
                  {pos.current_price !== null ? `${pos.current_price} PLN` : '—'}
                </td>
                <td style={{ padding: '8px' }}>{pos.value !== null ? `${pos.value} PLN` : '—'}</td>
                <td
                  style={{
                    padding: '8px',
                    color: pos.profit >= 0 ? '#4CAF50' : '#FF5252',
                  }}
                >
                  {pos.profit !== null ? `${pos.profit} PLN (${pos.profit_pct}%)` : '—'}
                </td>
                <td style={{ padding: '8px' }}>{pos.buy_date}</td>
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
                    }}
                  >
                    🔍 Co z tym?
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
                    }}
                  >
                    Usuń
                  </button>
                </td>
              </tr>
              {openAnalysisId === pos.id && (
                <tr style={{ borderBottom: '1px solid #444' }}>
                  <td colSpan={9} style={{ padding: '15px', background: '#2a2a2a' }}>
                    <div style={{ marginBottom: '12px' }}>
                      <strong>Horyzont analizy dla {pos.ticker}:</strong>
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
                      placeholder='Dodatkowy kontekst (opcjonalnie), np. "rozważam czasowe wyjście pod konferencję X"'
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
                          <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', margin: 0, color: '#eee' }}>
                            {analysisResult.analysis}
                          </p>
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
                              <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', margin: 0, color: '#eee' }}>
                                {f.answer}
                              </p>
                            </div>
                          </div>
                        ))}

                        <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
                          <input
                            placeholder='Pytanie uzupełniające, np. "czy dokupić za nowe 5k?"'
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
          </tbody>
        </table>
      )}
    </div>
  )
}

export default PortfolioTracker
