import { useState, useEffect } from 'react'

const API = 'http://127.0.0.1:8000'

/*
  "Lusterko inwestora" - Twoje nawyki policzone z Twojej historii (bez AI, sama matematyka):
  zamknięte transakcje (częściowe zlecenia złączone), otwarte pozycje na minusie i "Moje pomysły".
  Wnioski to fakty liczone stałymi progami w backend/mirror.py - nie porady.
*/

const zl = (v, sign = false) =>
  v == null ? '—' : `${sign && v > 0 ? '+' : ''}${Math.round(v).toLocaleString('pl-PL')} zł`
const pct = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`)
const days = (v) => (v == null ? '—' : `${Math.round(v)} dni`)
const tone = (v) => (v == null ? 'var(--text-dim)' : v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'var(--text)')
const plDate = (iso) => (iso ? new Date(iso).toLocaleDateString('pl-PL', { day: 'numeric', month: 'short', year: 'numeric' }) : '—')
const ACCOUNTS = { zwykle: 'konto zwykłe', ike: 'IKE', ikze: 'IKZE' }

function Tile({ label, value, sub, color }) {
  return (
    <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)', padding: '12px 14px', minWidth: 0 }}>
      <div style={{ fontSize: 11.5, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: 0.6 }}>{label}</div>
      <div className="hl-num" style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function Section({ title, hint, children }) {
  return (
    <div className="hl-panel" style={{ padding: '16px 20px', marginBottom: 16 }}>
      <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>{title}</h3>
      {hint && <div className="hl-adv-hint" style={{ margin: '0 0 10px' }}>{hint}</div>}
      {children}
    </div>
  )
}

function GroupTable({ rows, minSample }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="hl-table">
        <thead>
          <tr>
            <th>Grupa</th>
            <th style={{ textAlign: 'right' }}>Transakcji</th>
            <th style={{ textAlign: 'right' }}>Trafione</th>
            <th style={{ textAlign: 'right' }}>Średni zysk</th>
            <th style={{ textAlign: 'right' }}>Średnia strata</th>
            <th style={{ textAlign: 'right' }}>Wynik łącznie</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, s]) => (
            <tr key={label} style={{ opacity: s.count ? 1 : 0.5 }}>
              <td>
                {label}
                {s.count > 0 && s.count < minSample && <span className="hl-adv-vmeta"> · mało danych</span>}
              </td>
              <td className="hl-num" style={{ textAlign: 'right' }}>{s.count}</td>
              <td className="hl-num" style={{ textAlign: 'right' }}>{s.win_rate != null ? `${s.win_rate}%` : '—'}</td>
              <td className="hl-num" style={{ textAlign: 'right', color: 'var(--up)' }}>{pct(s.avg_win_pct)}</td>
              <td className="hl-num" style={{ textAlign: 'right', color: 'var(--down)' }}>{pct(s.avg_loss_pct)}</td>
              <td className="hl-num" style={{ textAlign: 'right', color: tone(s.total_pln), fontWeight: 600 }}>{zl(s.total_pln, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TradeList({ trades }) {
  return trades.map((t) => (
    <div key={`${t.ticker}-${t.buy_date}-${t.sell_date}-${t.account}`}
      style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 13, padding: '4px 0' }}>
      <span>
        <b className="hl-num">{t.ticker.replace(/\.WA$/, '')}</b>{' '}
        <span style={{ color: 'var(--text-dim)' }}>{plDate(t.sell_date)} · {days(t.days)}</span>
      </span>
      <span className="hl-num" style={{ color: tone(t.profit_pln) }}>{zl(t.profit_pln, true)} ({pct(t.return_pct)})</span>
    </div>
  ))
}

export default function MirrorPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch(`${API}/api/mirror`)
      .then(async (r) => {
        const d = await r.json()
        if (!r.ok) throw new Error(d.detail || 'Nie udało się policzyć statystyk.')
        setData(d)
      })
      .catch((e) => setError(e.message === 'Failed to fetch' ? 'Backend nie odpowiada - czy jest uruchomiony?' : e.message))
  }, [])

  if (error) return <div className="hl-adv-error">{error}</div>
  if (!data) return <div className="hl-panel" style={{ padding: 28, color: 'var(--text-dim)' }}>Liczę statystyki z Twojej historii…</div>

  const s = data.summary
  if (s.count === 0) {
    return (
      <div className="hl-panel" style={{ padding: 28, color: 'var(--text-muted)' }}>
        Nie ma jeszcze zamkniętych transakcji - Lusterko zacznie działać po pierwszych sprzedażach.
      </div>
    )
  }

  return (
    <div>
      <div className="hl-panel" style={{ padding: '20px 24px', marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 17 }}>Lusterko inwestora</h2>
        <div className="hl-adv-hint" style={{ margin: '4px 0 14px' }}>
          Twoje nawyki policzone z Twojej historii - bez AI, sama matematyka. {s.count} zamkniętych transakcji
          ({plDate(data.period[0])} – {plDate(data.period[1])}; zlecenia rozbite przez XTB na części liczą się jako jedna decyzja)
          oraz otwarte pozycje na minusie, bo strata, którą trzymasz, nie ma jeszcze sprzedaży.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
          <Tile label="Trafione" value={`${s.win_rate}%`} sub={`${s.wins} zysków · ${s.losses} strat`} />
          <Tile label="Wynik łącznie" value={zl(s.total_pln, true)} color={tone(s.total_pln)} sub="zamknięte transakcje" />
          <Tile label="Średni zysk" value={pct(s.avg_win_pct)} color="var(--up)" sub={`${zl(s.avg_win_pln, true)} · typowo ${days(s.median_days_win)}`} />
          <Tile label="Średnia strata" value={pct(s.avg_loss_pct)} color="var(--down)" sub={`${zl(s.avg_loss_pln, true)} · typowo ${days(s.median_days_loss)}`} />
          {s.profit_factor != null && (
            <Tile label="Zysk na 1 zł straty" value={`${s.profit_factor.toFixed(2)} zł`}
              sub={s.profit_factor >= 1 ? 'zyski przeważają nad stratami' : 'straty przeważają nad zyskami'} />
          )}
        </div>
        {s.small_sample && (
          <div className="hl-adv-hint">Tylko {s.count} transakcji - przy tak małej próbie wyniki to w dużej mierze przypadek.</div>
        )}
      </div>

      {data.habits.length > 0 && (
        <Section title="Co widać w Twojej historii" hint="Fakty policzone stałymi progami - nie porady. Decyzja, co z nimi zrobić, należy do Ciebie.">
          {data.habits.map((h) => (
            <div key={h.key} style={{
              borderLeft: `4px solid ${h.kind === 'good' ? 'var(--up)' : 'var(--warn)'}`,
              background: h.kind === 'good' ? 'rgba(0,245,160,0.06)' : 'rgba(251,191,36,0.08)',
              padding: '10px 14px', borderRadius: 'var(--radius-sm)', marginBottom: 8, fontSize: 13.5, lineHeight: 1.55,
            }}>
              {h.kind === 'good' ? '✓ ' : '⚠️ '}{h.text}
            </div>
          ))}
        </Section>
      )}

      {data.open_losers.length > 0 && (
        <Section title="Otwarte pozycje na minusie"
          hint={`Łącznie ${zl(data.open_losers_total_pln, true)} na papierze. „Dni” to średni czas trzymania (ważony kwotą, gdy kupowałeś w kilku transzach). Dla porównania: typowy zysk zamykasz po ${days(s.median_days_win)}.`}>
          <div style={{ overflowX: 'auto' }}>
            <table className="hl-table">
              <thead>
                <tr>
                  <th>Spółka</th><th>Konto</th>
                  <th style={{ textAlign: 'right' }}>Trzymasz</th>
                  <th style={{ textAlign: 'right' }}>Wynik</th>
                  <th style={{ textAlign: 'right' }}>W złotych</th>
                </tr>
              </thead>
              <tbody>
                {data.open_losers.map((x) => (
                  <tr key={`${x.ticker}-${x.account}`}>
                    <td><b className="hl-num">{x.ticker}</b> <span style={{ color: 'var(--text-dim)' }}>{x.name !== x.ticker ? x.name : ''}</span></td>
                    <td style={{ color: 'var(--text-dim)' }}>{ACCOUNTS[x.account] || x.account}</td>
                    <td className="hl-num" style={{ textAlign: 'right', color: x.days > (s.median_days_win || 1e9) ? 'var(--warn)' : 'var(--text)' }}>{days(x.days)}</td>
                    <td className="hl-num" style={{ textAlign: 'right', color: 'var(--down)' }}>{pct(x.return_pct)}</td>
                    <td className="hl-num" style={{ textAlign: 'right', color: 'var(--down)' }}>{zl(x.profit_pln, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      <Section title="Gdzie idzie Ci lepiej" hint={`Grupa z mniej niż ${data.min_sample} transakcjami jest oznaczona „mało danych” - tam różnice mogą być przypadkowe.`}>
        <div className="hl-adv-label" style={{ margin: '4px 0 2px' }}>Rynek</div>
        <GroupTable minSample={data.min_sample} rows={Object.entries(data.by_market)} />
        <div className="hl-adv-label" style={{ margin: '16px 0 2px' }}>Jak długo trzymałeś</div>
        <GroupTable minSample={data.min_sample} rows={data.by_hold.map((b) => [b.label, b])} />
        <div className="hl-adv-label" style={{ margin: '16px 0 2px' }}>Konto</div>
        <GroupTable minSample={data.min_sample} rows={Object.entries(data.by_account).map(([a, v]) => [ACCOUNTS[a] || a, v])} />
      </Section>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginBottom: 16 }}>
        <Section title="Największe zyski"><TradeList trades={data.biggest_wins.filter((t) => t.profit_pln > 0)} /></Section>
        <Section title="Największe straty"><TradeList trades={data.biggest_losses.filter((t) => t.profit_pln < 0)} /></Section>
      </div>

      <Section title="💡 Moje pomysły - na czym się opierały"
        hint="Rozliczone „Moje pomysły” (po terminie), pogrupowane według podstawy wybranej przy zapisie.">
        {data.ideas.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', fontSize: 13 }}>
            Pojawi się, gdy minie termin pierwszych pomysłów. Przy zapisie wybieraj „Na czym się opierasz?” -
            po kilkudziesięciu pomysłach zobaczysz, czy lepiej wychodzą Ci np. raporty, czy przeczucia po newsie.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="hl-table">
              <thead>
                <tr>
                  <th>Podstawa</th>
                  <th style={{ textAlign: 'right' }}>Pomysłów</th>
                  <th style={{ textAlign: 'right' }}>Trafione</th>
                  <th style={{ textAlign: 'right' }}>Pobiło rynek</th>
                  <th style={{ textAlign: 'right' }}>Średnio vs rynek</th>
                </tr>
              </thead>
              <tbody>
                {data.ideas.map((g) => (
                  <tr key={g.basis}>
                    <td>{g.label}{g.small_sample && <span className="hl-adv-vmeta"> · mało danych</span>}</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{g.count}</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{g.hit}/{g.count}</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{g.beat_market}/{g.count}</td>
                    <td className="hl-num" style={{ textAlign: 'right', color: tone(g.avg_vs_market_pct) }}>
                      {g.avg_vs_market_pct == null ? '—' : `${g.avg_vs_market_pct > 0 ? '+' : ''}${g.avg_vs_market_pct.toFixed(2)} pp`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <div className="hl-adv-hint" style={{ textAlign: 'center', marginBottom: 8 }}>
        Wyniki w złotych z historii sprzedaży (po przewalutowaniu, przed podatkiem). To statystyka Twojej przeszłości,
        nie porada inwestycyjna.
      </div>
    </div>
  )
}
