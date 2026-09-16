import { useState, useEffect } from 'react'

const API_URL = 'http://127.0.0.1:8000'

function PriceAlerts() {
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [form, setForm] = useState({ ticker: '', condition: 'below', target_price: '' })
  const [adding, setAdding] = useState(false)
  const [checkingNow, setCheckingNow] = useState(false)

  const fetchAlerts = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/alerts`)
      if (!res.ok) throw new Error('Nie udało się pobrać alertów.')
      const data = await res.json()
      setAlerts(data.alerts)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const checkNow = async () => {
    setCheckingNow(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/alerts/check-now`, { method: 'POST' })
      if (!res.ok) throw new Error('Nie udało się sprawdzić alertów.')
      const data = await res.json()
      setAlerts(data.alerts)
    } catch (err) {
      setError(err.message)
    } finally {
      setCheckingNow(false)
    }
  }

  useEffect(() => {
    fetchAlerts()
    // Odświeżaj co 60s, żeby widzieć aktualne ceny i nowo wyzwolone alerty bez ręcznego F5
    const interval = setInterval(fetchAlerts, 60000)
    return () => clearInterval(interval)
  }, [])

  const handleAdd = async (e) => {
    e.preventDefault()
    if (!form.ticker.trim() || !form.target_price) {
      setError('Podaj ticker i cenę docelową.')
      return
    }
    setAdding(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/alerts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: form.ticker,
          condition: form.condition,
          target_price: parseFloat(form.target_price),
        }),
      })
      if (!res.ok) throw new Error('Nie udało się dodać alertu.')
      setForm({ ticker: '', condition: 'below', target_price: '' })
      fetchAlerts()
    } catch (err) {
      setError(err.message)
    } finally {
      setAdding(false)
    }
  }

  const handleDelete = async (id) => {
    try {
      const res = await fetch(`${API_URL}/api/alerts/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Nie udało się usunąć alertu.')
      fetchAlerts()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleReset = async (id) => {
    try {
      const res = await fetch(`${API_URL}/api/alerts/${id}/reset`, { method: 'POST' })
      if (!res.ok) throw new Error('Nie udało się zresetować alertu.')
      fetchAlerts()
    } catch (err) {
      setError(err.message)
    }
  }

  const inputStyle = {
    padding: '10px',
    fontSize: '14px',
    borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border)',
    color: 'var(--text)',
    backgroundColor: 'var(--bg-elevated)',
  }

  const active = alerts.filter((a) => !a.triggered)
  const triggered = alerts.filter((a) => a.triggered)

  return (
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>🔔 Alerty cenowe</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Ustaw próg ceny dla dowolnego tickera (nie musi być w portfelu). Backend sprawdza ceny co 15
        minut — działa tylko, gdy aplikacja jest uruchomiona. Powiadomienie zawsze pojawi się tutaj,
        a na e-mail tylko jeśli skonfigurujesz SMTP w pliku .env.
      </p>

      <form
        onSubmit={handleAdd}
        style={{ display: 'flex', gap: '10px', marginBottom: '25px', flexWrap: 'wrap' }}
      >
        <input
          placeholder="Ticker (np. 1AT.WA)"
          value={form.ticker}
          onChange={(e) => setForm({ ...form, ticker: e.target.value })}
          style={{ ...inputStyle, width: '150px' }}
        />
        <select
          value={form.condition}
          onChange={(e) => setForm({ ...form, condition: e.target.value })}
          style={{ ...inputStyle, width: '160px' }}
        >
          <option value="below">Spadnie poniżej</option>
          <option value="above">Wzrośnie powyżej</option>
        </select>
        <input
          placeholder="Cena docelowa"
          type="number"
          step="any"
          value={form.target_price}
          onChange={(e) => setForm({ ...form, target_price: e.target.value })}
          style={{ ...inputStyle, width: '130px' }}
        />
        <button
          type="submit"
          disabled={adding} className="hl-btn hl-btn-primary"
          style={{
            padding: '10px 20px',
            background: 'var(--accent)',
            color: 'white',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            cursor: adding ? 'not-allowed' : 'pointer',
          }}
        >
          {adding ? 'Dodaję...' : 'Dodaj alert'}
        </button>
      </form>

      {error && <div style={{ color: 'var(--down)', marginBottom: '15px' }}>{error}</div>}
      {loading && alerts.length === 0 && <div style={{ color: 'var(--text-muted)' }}>Ładowanie...</div>}

      {triggered.length > 0 && (
        <>
          <h3 style={{ color: 'var(--warn)', marginBottom: '10px' }}>🔥 Wyzwolone ({triggered.length})</h3>
          {triggered.map((a) => (
            <div
              key={a.id}
              style={{
                background: 'rgba(251,191,36,0.08)',
                borderLeft: '4px solid var(--warn)',
                padding: '12px 15px',
                borderRadius: 'var(--radius)',
                marginBottom: '10px',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: '10px',
              }}
            >
              <div>
                <strong>{a.name} ({a.ticker})</strong> —{' '}
                {a.condition === 'below' ? 'spadła poniżej' : 'wzrosła powyżej'} {a.target_price} {a.currency}
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Wyzwolone {a.triggered_at} przy cenie {a.triggered_price} {a.currency}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="hl-btn"
                  onClick={() => handleReset(a.id)}
                  style={{ background: 'var(--bg-elevated)', color: 'white', border: 'none', borderRadius: 'var(--radius-sm)', padding: '6px 12px', cursor: 'pointer' }}
                >
                  Resetuj
                </button>
                <button className="hl-btn"
                  onClick={() => handleDelete(a.id)}
                  style={{ background: 'var(--down)', color: 'white', border: 'none', borderRadius: 'var(--radius-sm)', padding: '6px 12px', cursor: 'pointer' }}
                >
                  Usuń
                </button>
              </div>
            </div>
          ))}
        </>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
        <h3 style={{ margin: 0 }}>Aktywne ({active.length})</h3>
        <button
          onClick={checkNow}
          disabled={checkingNow} className="hl-btn"
          style={{
            padding: '8px 16px',
            background: 'var(--bg-elevated)',
            color: 'white',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            cursor: checkingNow ? 'not-allowed' : 'pointer',
            fontSize: '13px',
          }}
        >
          {checkingNow ? 'Sprawdzam...' : '🔄 Sprawdź teraz'}
        </button>
      </div>
      {active.length === 0 ? (
        <div style={{ color: 'var(--text-muted)' }}>Brak aktywnych alertów.</div>
      ) : (
        <table className="hl-table">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-bright)', textAlign: 'left' }}>
              <th style={{ padding: '8px' }}>Spółka</th>
              <th style={{ padding: '8px' }}>Warunek</th>
              <th style={{ padding: '8px' }}>Aktualna cena</th>
              <th style={{ padding: '8px' }}></th>
            </tr>
          </thead>
          <tbody>
            {active.map((a) => (
              <tr key={a.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px' }}>
                  <div style={{ fontWeight: 'bold' }}>{a.name}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-dim)' }}>{a.ticker}</div>
                </td>
                <td style={{ padding: '8px' }}>
                  {a.condition === 'below' ? '↓ poniżej' : '↑ powyżej'} {a.target_price} {a.currency}
                </td>
                <td style={{ padding: '8px' }}>
                  {a.current_price !== null ? `${a.current_price} ${a.currency}` : '—'}
                </td>
                <td style={{ padding: '8px' }}>
                  <button className="hl-btn"
                    onClick={() => handleDelete(a.id)}
                    style={{ background: 'var(--down)', color: 'white', border: 'none', borderRadius: 'var(--radius-sm)', padding: '5px 10px', cursor: 'pointer' }}
                  >
                    Usuń
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '25px' }}>
        ⚠️ Alerty działają tylko gdy backend jest uruchomiony (nie w tle na serwerze/w chmurze). Aby
        włączyć powiadomienia e-mail, dodaj do backend/.env: SMTP_HOST, SMTP_PORT, SMTP_USER,
        SMTP_PASSWORD, SMTP_TO.
      </p>
    </div>
  )
}

export default PriceAlerts