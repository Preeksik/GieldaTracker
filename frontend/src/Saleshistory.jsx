import { useState, useEffect } from 'react'

const API_URL = 'http://127.0.0.1:8000'

function SalesHistory() {
  const [sales, setSales] = useState([])
  const [summary, setSummary] = useState(null)
  const [year, setYear] = useState(new Date().getFullYear())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const fetchSales = async () => {
    setLoading(true)
    setError('')
    try {
      const [salesRes, summaryRes] = await Promise.all([
        fetch(`${API_URL}/api/sales`),
        fetch(`${API_URL}/api/sales/pit38-summary?year=${year}`),
      ])
      if (!salesRes.ok) throw new Error('Nie udało się pobrać historii sprzedaży.')
      const salesData = await salesRes.json()
      setSales(salesData.sales)

      if (summaryRes.ok) {
        setSummary(await summaryRes.json())
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSales()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year])

  const handleDelete = async (id) => {
    try {
      const res = await fetch(`${API_URL}/api/sales/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Nie udało się usunąć wpisu.')
      fetchSales()
    } catch (err) {
      setError(err.message)
    }
  }

  const sortedSales = [...sales].sort((a, b) => (a.sell_date < b.sell_date ? 1 : -1))
  const availableYears = [...new Set(sales.map((s) => s.sell_date.slice(0, 4)))].sort().reverse()
  if (!availableYears.includes(String(year))) availableYears.unshift(String(year))

  return (
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>💰 Zrealizowane sprzedaże</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Realny (zrealizowany) zysk/strata z faktycznie zamkniętych pozycji - w odróżnieniu od
        "papierowego" zysku widocznego w zakładce Portfel, który zmienia się z każdym ruchem ceny.
      </p>

      {error && <div style={{ color: 'var(--down)', marginBottom: '15px' }}>{error}</div>}

      <div style={{ display: 'flex', gap: '8px', marginBottom: '20px', flexWrap: 'wrap' }}>
        {availableYears.map((y) => (
          <button className="hl-btn"
            key={y}
            onClick={() => setYear(parseInt(y))}
            style={{
              padding: '8px 16px',
              background: year === parseInt(y) ? 'var(--accent)' : 'var(--bg-elevated)',
              color: 'white',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
            }}
          >
            {y}
          </button>
        ))}
      </div>

      {loading && <div style={{ color: 'var(--text-muted)', marginBottom: '15px' }}>Ładowanie...</div>}

      {summary && (summary.zwykle.transactions_count > 0 || summary.ike_ikze.transactions_count > 0) && (
        <div style={{ display: 'flex', gap: '15px', marginBottom: '25px', flexWrap: 'wrap' }}>
          <div style={{ background: 'var(--bg-panel)', padding: '14px 18px', borderRadius: 'var(--radius)', minWidth: '260px' }}>
            <div style={{ fontWeight: 'bold', marginBottom: '6px' }}>
              Zwykłe konto ({summary.zwykle.transactions_count} transakcji, {summary.year})
            </div>
            <div>Przychód: {summary.zwykle.total_proceeds_pln.toFixed(2)} PLN</div>
            <div>Koszt: {summary.zwykle.total_cost_pln.toFixed(2)} PLN</div>
            <div style={{ color: summary.zwykle.total_profit_pln >= 0 ? 'var(--up)' : 'var(--down)' }}>
              Zysk/strata: {summary.zwykle.total_profit_pln.toFixed(2)} PLN
            </div>
            <div style={{ color: 'var(--warn)' }}>Orient. podatek Belki: {summary.zwykle.total_tax_pln.toFixed(2)} PLN</div>
            <div style={{ color: summary.zwykle.total_profit_after_tax_pln >= 0 ? 'var(--up)' : 'var(--down)' }}>
              Po podatku: {summary.zwykle.total_profit_after_tax_pln.toFixed(2)} PLN
            </div>
          </div>

          {summary.ike_ikze.transactions_count > 0 && (
            <div style={{ background: 'var(--bg-panel)', padding: '14px 18px', borderRadius: 'var(--radius)', minWidth: '260px' }}>
              <div style={{ fontWeight: 'bold', marginBottom: '6px' }}>
                IKE/IKZE ({summary.ike_ikze.transactions_count} transakcji, {summary.year})
              </div>
              <div>Przychód: {summary.ike_ikze.total_proceeds_pln.toFixed(2)} PLN</div>
              <div>Koszt: {summary.ike_ikze.total_cost_pln.toFixed(2)} PLN</div>
              <div style={{ color: summary.ike_ikze.total_profit_pln >= 0 ? 'var(--up)' : 'var(--down)' }}>
                Zysk/strata: {summary.ike_ikze.total_profit_pln.toFixed(2)} PLN
              </div>
              <div style={{ color: 'var(--up)', fontSize: '12px' }}>✓ zwolnione z podatku Belki</div>
            </div>
          )}
        </div>
      )}

      {sortedSales.length === 0 && !loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Brak zarejestrowanych sprzedaży. Sprzedaż zarejestrujesz w zakładce Portfel, rozwijając daną transakcję.</div>
      ) : (
        <table className="hl-table">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-bright)', textAlign: 'left' }}>
              <th style={{ padding: '8px' }}>Spółka</th>
              <th style={{ padding: '8px' }}>Ilość</th>
              <th style={{ padding: '8px' }}>Kupno → Sprzedaż</th>
              <th style={{ padding: '8px' }}>Zysk/strata</th>
              <th style={{ padding: '8px' }}>Konto</th>
              <th style={{ padding: '8px' }}></th>
            </tr>
          </thead>
          <tbody>
            {sortedSales.map((s) => (
              <tr key={s.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px' }}>
                  <div style={{ fontWeight: 'bold' }}>{s.name}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-dim)' }}>{s.ticker}</div>
                </td>
                <td style={{ padding: '8px' }}>{s.quantity}</td>
                <td style={{ padding: '8px', fontSize: '13px' }}>
                  {s.buy_price} {s.buy_currency} ({s.buy_date}) → {s.sell_price} {s.sell_currency} ({s.sell_date})
                </td>
                <td style={{ padding: '8px', color: s.realized_profit_pln >= 0 ? 'var(--up)' : 'var(--down)' }}>
                  {s.realized_profit_pln.toFixed(2)} PLN
                </td>
                <td style={{ padding: '8px' }}>{s.account === 'zwykle' ? 'Zwykłe' : s.account.toUpperCase()}</td>
                <td style={{ padding: '8px' }}>
                  <button className="hl-btn"
                    onClick={() => handleDelete(s.id)}
                    style={{ background: 'var(--down)', color: 'white', border: 'none', borderRadius: 'var(--radius-sm)', padding: '5px 10px', cursor: 'pointer', fontSize: '12px' }}
                  >
                    Usuń
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '11px', marginTop: '20px' }}>
        ⚠️ Podatek Belki to orientacyjny szacunek (19% od netto rocznego wyniku na koncie zwykłym) -
        do oficjalnego rozliczenia użyj PIT-38 z Twojego brokera (np. XTB), to tylko podgląd.
      </p>
    </div>
  )
}

export default SalesHistory