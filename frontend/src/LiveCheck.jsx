import { useState } from 'react'

const API_URL = 'http://127.0.0.1:8000'

/*
  "Sprawdź na dziś" - szybki podgląd gry na papierze na świeżych cenach z Yahoo
  (w trakcie sesji z opóźnieniem ok. 15 min). Kilka sekund, NIC nie zapisuje.
  Oficjalny wynik to nadal wieczorne rozliczenie - w trakcie sesji liczby mogą się jeszcze zmienić.
*/

const COLORS = { A1: '#00F5A0', A2: '#38BDF8', A3: '#A78BFA', hold: '#94A3B8' }

const pct = (x, d = 2) => (x == null ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(d)}%`)
const zl = (x) => (x == null ? '—' : `${Math.round(x).toLocaleString('pl-PL')} zł`)
const tone = (x) => (x == null ? 'var(--text-dim)' : x >= 0 ? 'var(--up)' : 'var(--down)')
const tick = (t) => t.replace(/\.WA$/, '')
const time = (iso) => new Date(iso).toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' })
const day = (iso) => new Date(iso).toLocaleDateString('pl-PL', { weekday: 'long', day: 'numeric', month: 'long' })

function Tickers({ h }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '2px 16px', padding: '6px 0 10px' }}>
      {h.tickers.map((t) => (
        <div key={t.ticker} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5 }}>
          <span className="hl-num">{tick(t.ticker)}</span>
          <span className="hl-num">
            <span style={{ color: tone(t.today), marginRight: 8 }} title="dzisiaj">{pct(t.today, 1)}</span>
            <span style={{ color: tone(t.return) }} title="od kupna">{pct(t.return, 1)}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

export default function LiveCheck() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(null)

  const check = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/lab/paper/live${data ? '?fresh=true' : ''}`)
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Nie udało się sprawdzić gry.')
      setData(d)
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? 'Backend nie odpowiada - czy jest uruchomiony?' : err.message)
    } finally {
      setLoading(false)
    }
  }

  const held = (data?.strategies || []).filter((s) => s.held)
  const hold = held[0]?.held
  const pending = (data?.strategies || []).filter((s) => s.pending)

  return (
    <div className="hl-panel" style={{ padding: '16px 20px', marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ flex: '1 1 300px', minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>Jak idzie dzisiaj?</h3>
          <div className="hl-adv-hint" style={{ margin: '4px 0 0' }}>
            Szybki podgląd na najświeższych cenach (kilka sekund). Niczego nie zapisuje - oficjalny wynik
            liczy wieczorne rozliczenie.
          </div>
        </div>
        <button className="hl-btn" onClick={check} disabled={loading}>
          {loading ? 'Sprawdzam…' : data ? 'Sprawdź ponownie' : 'Sprawdź na dziś'}
        </button>
      </div>

      {error && <div className="hl-adv-error" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}

      {data && (
        <>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '12px 0 8px' }}>
            Stan na {time(data.checked_at)} · ceny z sesji: {day(data.last_session)}
            {data.session_open
              ? <span style={{ color: 'var(--warn)' }}> · sesja trwa - ceny z opóźnieniem ok. 15 min i jeszcze się zmienią</span>
              : ' · sesja zamknięta'}
            {data.cached && ' · (sprzed chwili)'}
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table className="hl-table">
              <thead>
                <tr>
                  <th>Strategia</th>
                  <th style={{ textAlign: 'right' }}>Dzisiaj</th>
                  <th style={{ textAlign: 'right' }}>Od kupna</th>
                  <th style={{ textAlign: 'right' }}>Z {zl(data.start_value)} jest</th>
                  <th style={{ textAlign: 'right' }}>vs trzymanie</th>
                </tr>
              </thead>
              <tbody>
                {held.map((s) => {
                  const h = s.held
                  const diff = h.return != null && h.hold_return != null ? h.return - h.hold_return : null
                  return [
                    <tr key={s.key} onClick={() => setOpen(open === s.key ? null : s.key)} style={{ cursor: 'pointer' }}
                      title="Kliknij, żeby zobaczyć spółki">
                      <td>
                        <span style={{ color: COLORS[s.key], fontWeight: 700 }}>{s.key}</span>
                        <span style={{ color: 'var(--text-dim)', fontSize: 12 }}> {open === s.key ? '▾' : '▸'} {h.tickers.length} spółek</span>
                        {h.bought_today && <span className="hl-adv-hint"> · kupione dziś na zamknięciu</span>}
                      </td>
                      <td className="hl-num" style={{ textAlign: 'right', color: tone(h.today) }}>{pct(h.today)}</td>
                      <td className="hl-num" style={{ textAlign: 'right', color: tone(h.return) }}>{pct(h.return)}</td>
                      <td className="hl-num" style={{ textAlign: 'right' }}
                        title={h.value == null ? 'Poprzedni miesiąc nie jest jeszcze rozliczony - kliknij „Rozlicz teraz”.' : ''}>
                        {zl(h.value)}
                      </td>
                      <td className="hl-num" style={{ textAlign: 'right', color: tone(diff), fontWeight: 600 }}>{pct(diff)}</td>
                    </tr>,
                    open === s.key && (
                      <tr key={`${s.key}-t`}>
                        <td colSpan={5} style={{ paddingTop: 0 }}>
                          <div className="hl-adv-hint" style={{ margin: '0 0 2px' }}>dzisiaj · od kupna ({h.entry_date})</div>
                          <Tickers h={h} />
                          {h.missing.length > 0 && (
                            <div className="hl-adv-hint" style={{ margin: 0, color: 'var(--warn)' }}>
                              Brak świeżych cen: {h.missing.map(tick).join(', ')} - pominięte w wyniku.
                            </div>
                          )}
                        </td>
                      </tr>
                    ),
                  ]
                })}
                {hold && (
                  <tr>
                    <td><span style={{ color: COLORS.hold, fontWeight: 700 }}>Trzymanie</span>
                      <span style={{ color: 'var(--text-dim)', fontSize: 12 }}> wszystkich spółek po równo</span></td>
                    <td className="hl-num" style={{ textAlign: 'right', color: tone(hold.hold_today) }}>{pct(hold.hold_today)}</td>
                    <td className="hl-num" style={{ textAlign: 'right', color: tone(hold.hold_return) }}>{pct(hold.hold_return)}</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{zl(hold.hold_value)}</td>
                    <td />
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {pending.length > 0 && (
            <div className="hl-adv-hint">
              Nowe typy ({pending.map((s) => s.key).join(', ')}) czekają na kupno na zamknięciu najbliższej sesji -
              do tego czasu liczy się poprzedni skład.
            </div>
          )}
          {held.some((s) => s.held.value == null) && (
            <div className="hl-adv-hint">
              „—” w kolumnie wartości: poprzedni miesiąc nie jest jeszcze rozliczony. Kliknij „Rozlicz teraz” u góry.
            </div>
          )}
        </>
      )}
    </div>
  )
}
