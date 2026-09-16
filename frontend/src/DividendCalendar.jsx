import { useState, useEffect, Fragment } from 'react'

const API_URL = 'http://127.0.0.1:8000'

function DividendCalendar() {
  const [positions, setPositions] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState('szacowane') // 'szacowane' | 'realne'
  const [expandedTicker, setExpandedTicker] = useState(null)

  const fetchDividends = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/dividends`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się pobrać danych o dywidendach.')
      }
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
    fetchDividends()
  }, [])

  const toggleTabStyle = (active) => ({
    padding: '8px 16px',
    background: active ? 'var(--accent)' : 'var(--bg-elevated)',
    color: 'white',
    border: 'none',
    borderRadius: 'var(--radius-sm)',
    cursor: 'pointer',
  })

  const withHistory = positions.filter((p) => p.has_dividend_history)
  const withoutHistory = positions.filter((p) => !p.has_dividend_history)

  return (
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>📅 Kalendarz dywidend</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Dywidendy to zmienna, niepewna rzecz - dlatego rozdzielamy to co Yahoo Finance
        <strong> szacuje na przyszłość</strong> od tego co <strong>realnie zostało wypłacone</strong> w
        ostatnich 12 miesiącach.
      </p>

      {error && <div style={{ color: 'var(--down)', marginBottom: '20px' }}>{error}</div>}
      {loading && <div style={{ color: 'var(--text-muted)', marginBottom: '20px' }}>Ładowanie danych o dywidendach...</div>}

      {summary && (
        <div
          style={{
            display: 'flex',
            gap: '25px',
            marginBottom: '20px',
            background: 'var(--bg-elevated)',
            padding: '15px',
            borderRadius: 'var(--radius)',
            flexWrap: 'wrap',
          }}
        >
          <div>
            💰 Otrzymane (realne, 12 mies.):{' '}
            <strong style={{ color: 'var(--up)' }}>{summary.total_realized_last_12mo_pln.toFixed(2)} PLN</strong>
          </div>
          <div>
            📈 Szacowany roczny dochód:{' '}
            <strong style={{ color: 'var(--warn)' }}>{summary.total_annual_estimate_pln.toFixed(2)} PLN</strong>
            <span style={{ color: 'var(--text-dim)', fontSize: '12px' }}> (estymacja)</span>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
        <button className="hl-btn" onClick={() => setView('szacowane')} style={toggleTabStyle(view === 'szacowane')}>
          🔮 Szacowana dywidenda
        </button>
        <button className="hl-btn" onClick={() => setView('realne')} style={toggleTabStyle(view === 'realne')}>
          ✅ Realna dywidenda (wypłacona)
        </button>
      </div>

      {withHistory.length === 0 && !loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Żadna ze spółek w portfelu nie ma danych o dywidendach w Yahoo Finance.</div>
      ) : (
        <table className="hl-table">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-bright)', textAlign: 'left' }}>
              <th style={{ padding: '8px' }}>Spółka</th>
              <th style={{ padding: '8px' }}>Ilość</th>
              {view === 'szacowane' ? (
                <>
                  <th style={{ padding: '8px' }}>Najbliższa data odcięcia</th>
                  <th style={{ padding: '8px' }}>Szac. kwota (na akcję)</th>
                  <th style={{ padding: '8px' }}>Szac. wpływ (ta wypłata)</th>
                  <th style={{ padding: '8px' }}>Szac. roczny dochód</th>
                </>
              ) : (
                <>
                  <th style={{ padding: '8px' }}>Wypłat (12 mies.)</th>
                  <th style={{ padding: '8px' }}>Suma otrzymana</th>
                  <th style={{ padding: '8px' }}></th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {withHistory.map((p) => (
              <Fragment key={p.ticker}>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px' }}>
                    <div style={{ fontWeight: 'bold' }}>{p.name}</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-dim)' }}>{p.ticker}</div>
                  </td>
                  <td style={{ padding: '8px' }}>{p.quantity}</td>

                  {view === 'szacowane' ? (
                    <>
                      <td style={{ padding: '8px' }}>{p.next_ex_date || '— brak danych —'}</td>
                      <td style={{ padding: '8px' }}>
                        {p.next_amount_estimate_per_share !== null
                          ? `${p.next_amount_estimate_per_share} ${p.currency}`
                          : '—'}
                      </td>
                      <td style={{ padding: '8px' }}>
                        {p.next_amount_estimate_total !== null
                          ? `${p.next_amount_estimate_total} ${p.currency}`
                          : '—'}
                      </td>
                      <td style={{ padding: '8px', color: 'var(--warn)' }}>
                        {p.annual_estimate_total_pln !== null ? `≈ ${p.annual_estimate_total_pln} PLN` : '—'}
                      </td>
                    </>
                  ) : (
                    <>
                      <td style={{ padding: '8px' }}>{p.realized_dividends.length}</td>
                      <td style={{ padding: '8px', color: 'var(--up)' }}>
                        {p.realized_total_pln !== null ? `${p.realized_total_pln} PLN` : '—'}
                        {p.realized_total_native !== null && p.currency !== 'PLN' && (
                          <span style={{ color: 'var(--text-dim)', fontSize: '11px' }}>
                            {' '}
                            ({p.realized_total_native} {p.currency})
                          </span>
                        )}
                      </td>
                      <td style={{ padding: '8px' }}>
                        {p.realized_dividends.length > 0 && (
                          <button className="hl-btn"
                            onClick={() => setExpandedTicker(expandedTicker === p.ticker ? null : p.ticker)}
                            style={{
                              background: 'var(--bg-elevated)',
                              color: 'white',
                              border: 'none',
                              borderRadius: 'var(--radius-sm)',
                              padding: '5px 10px',
                              cursor: 'pointer',
                              fontSize: '12px',
                            }}
                          >
                            {expandedTicker === p.ticker ? 'Ukryj' : 'Szczegóły'}
                          </button>
                        )}
                      </td>
                    </>
                  )}
                </tr>

                {view === 'realne' && expandedTicker === p.ticker && (
                  <tr>
                    <td colSpan={5} style={{ padding: '10px 10px 10px 30px', background: 'var(--bg-deep)' }}>
                      {p.realized_dividends.map((d, idx) => (
                        <div key={idx} style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                          {d.date}: {d.amount_per_share} {p.currency}/akcję → łącznie {d.total_received} {p.currency}
                        </div>
                      ))}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}

      {withoutHistory.length > 0 && (
        <p style={{ color: 'var(--text-dim)', fontSize: '13px', marginTop: '20px' }}>
          Bez danych o dywidendzie (prawdopodobnie nie wypłacają): {withoutHistory.map((p) => p.ticker).join(', ')}
        </p>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '20px' }}>
        ⚠️ "Szacowana dywidenda" bazuje na estymacjach Yahoo Finance (roczna stawka, ostatnia wypłata) -
        to NIE jest gwarancja wypłaty ani jej wysokości. "Realna dywidenda" to twarde dane historyczne.
      </p>
    </div>
  )
}

export default DividendCalendar