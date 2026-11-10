import { useState, useEffect, useRef, useCallback } from 'react'
import { createChart, LineSeries, LineStyle } from 'lightweight-charts'
import LiveCheck from './LiveCheck'

const API_URL = 'http://127.0.0.1:8000'

/*
  Zakładka QuantPulse - gra na papierze bez terminala.
  Backend liczy wszystko wieczorem w tle (albo po "Rozlicz teraz") i zapisuje gotowy raport,
  ta zakładka tylko go czyta - dlatego otwiera się od razu.
  Nic tu nie jest kupowane: to zapis "na papierze" trzech zamrożonych strategii (A1/A2/A3).
*/

// lightweight-charts rysuje na canvasie - kolory muszą być literalnymi hexami
const COLORS = { A1: '#00F5A0', A2: '#38BDF8', A3: '#A78BFA', hold: '#94A3B8' }
const SHORT = {
  A1: 'Najbliżej rocznego szczytu',
  A2: 'Największy wzrost za 6 miesięcy',
  A3: 'Sieć neuronowa',
}
const EXPLAIN = {
  A1: 'Wybiera spółki, których cena jest najbliżej najwyższej ceny z ostatniego roku.',
  A2: 'Wybiera spółki, które najmocniej urosły w ostatnich ~6 miesiącach.',
  A3: 'Program, który sam nauczył się wybierać spółki na podstawie historii notowań.',
}

const pct = (x, digits = 1) => (x == null ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`)
const zl = (x) => (x == null ? '—' : `${Math.round(x).toLocaleString('pl-PL')} zł`)
const tick = (t) => t.replace(/\.WA$/, '')
const tone = (x) => (x == null ? 'var(--text-dim)' : x >= 0 ? 'var(--up)' : 'var(--down)')

function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('pl-PL', { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.toLocaleDateString('pl-PL', { day: 'numeric', month: 'short' })}, ${d.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' })}`
}

const STATUS = {
  'czeka na kupno': { label: 'czeka na kupno', color: 'var(--warn)' },
  otwarty: { label: 'trwa', color: 'var(--cyan)' },
  rozliczony: { label: 'rozliczony', color: 'var(--text-muted)' },
}

function StatusBadge({ status }) {
  const s = STATUS[status] || { label: status, color: 'var(--text-dim)' }
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, color: s.color, border: `1px solid ${s.color}`,
      borderRadius: 999, padding: '2px 8px', whiteSpace: 'nowrap', opacity: 0.9,
    }}>{s.label}</span>
  )
}

function StrategyCard({ s, startValue }) {
  const cur = s.current
  const diff = s.value != null && s.hold != null ? s.value - s.hold : null
  return (
    <div className="hl-panel" style={{ padding: '16px 18px', borderTop: `3px solid ${COLORS[s.key]}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>
          <span style={{ color: COLORS[s.key] }}>{s.key}</span> · {SHORT[s.key]}
        </div>
        {cur && <StatusBadge status={cur.status} />}
      </div>
      <div className="hl-adv-hint" style={{ margin: '4px 0 12px' }}>{EXPLAIN[s.key]}</div>

      {s.periods === 0 ? (
        <div style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          Wynik pojawi się po pierwszej sesji od kupna.
        </div>
      ) : (
        <>
          <div className="hl-num" style={{ fontSize: 24, fontWeight: 700 }}>{zl(s.value)}</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-dim)', marginTop: 2 }}>
            z {zl(startValue)} na starcie · {pct(s.value / startValue - 1)}
          </div>
          <div style={{ fontSize: 13, marginTop: 10 }}>
            Zwykłe trzymanie wszystkich spółek: <span className="hl-num">{zl(s.hold)}</span>
          </div>
          <div style={{ fontSize: 13, marginTop: 4, color: tone(diff), fontWeight: 600 }}>
            {diff == null ? '' : diff >= 0
              ? `Na razie lepiej od trzymania o ${zl(diff)}`
              : `Na razie gorzej od trzymania o ${zl(-diff)}`}
          </div>
          <div className="hl-adv-hint" style={{ marginTop: 6 }}>
            Miesiące lepsze od trzymania: {s.better} z {s.periods}
            {s.closed < s.periods ? ' (bieżący miesiąc jeszcze trwa)' : ''}
          </div>
        </>
      )}
    </div>
  )
}

function EquityChart({ curve }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current || curve.length < 2) return
    const chart = createChart(ref.current, {
      layout: { background: { type: 'solid', color: '#0E1524' }, textColor: '#94A3B8', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(148,163,184,0.05)' }, horzLines: { color: 'rgba(148,163,184,0.05)' } },
      rightPriceScale: { borderColor: '#1F2937' },
      timeScale: { borderColor: '#1F2937', fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: 1 },
      width: ref.current.clientWidth,
      height: 300,
      localization: { priceFormatter: (p) => `${Math.round(p).toLocaleString('pl-PL')} zł` },
    })
    for (const key of ['hold', 'A1', 'A2', 'A3']) {
      const pts = curve.filter((p) => p[key] != null).map((p) => ({ time: p.date, value: p[key] }))
      if (!pts.length) continue
      const series = chart.addSeries(LineSeries, {
        color: COLORS[key],
        lineWidth: key === 'hold' ? 2 : 2.5,
        lineStyle: key === 'hold' ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        title: key === 'hold' ? 'trzymanie' : key,
      })
      series.setData(pts)
    }
    chart.timeScale().fitContent()
    const onResize = () => ref.current && chart.applyOptions({ width: ref.current.clientWidth })
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.remove()
    }
  }, [curve])

  if (curve.length < 2) {
    return (
      <div style={{ padding: 28, textAlign: 'center', color: 'var(--text-dim)', fontSize: 13 }}>
        Wykres pojawi się po co najmniej dwóch sesjach od kupna.
      </div>
    )
  }
  return <div ref={ref} style={{ width: '100%' }} />
}

function CurrentPicks({ strategies }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
      {strategies.map((s) => {
        const cur = s.current
        if (!cur) return null
        const sorted = [...cur.tickers].sort((a, b) => (b.return ?? -1e9) - (a.return ?? -1e9))
        return (
          <div key={s.key} style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)', padding: '12px 14px' }}>
            <div style={{ fontWeight: 700, marginBottom: 2 }}>
              <span style={{ color: COLORS[s.key] }}>{s.key}</span>{' '}
              <span className="hl-num" style={{ color: tone(cur.return), fontSize: 13 }}>{cur.return != null ? pct(cur.return) : ''}</span>
            </div>
            <div className="hl-adv-hint" style={{ margin: '0 0 8px' }}>
              wskazanie {fmtDate(cur.decision_date)}
              {cur.entry_date ? ` · kupno ${fmtDate(cur.entry_date)}` : ' · kupno na zamknięciu najbliższej sesji'}
            </div>
            {sorted.map((t) => (
              <div key={t.ticker} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '3px 0' }}>
                <span className="hl-num">{tick(t.ticker)}</span>
                <span className="hl-num" style={{ color: tone(t.return) }}>{t.return != null ? pct(t.return) : '—'}</span>
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

function History({ history }) {
  if (!history.length) return null
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="hl-table">
        <thead>
          <tr>
            <th>Wskazanie</th><th>Strategia</th><th>Stan</th><th>Kupno → sprzedaż</th>
            <th style={{ textAlign: 'right' }}>Wynik</th><th style={{ textAlign: 'right' }}>Trzymanie</th>
            <th style={{ textAlign: 'right' }}>Różnica</th>
          </tr>
        </thead>
        <tbody>
          {history.map((h) => {
            const diff = h.return != null && h.hold_return != null ? h.return - h.hold_return : null
            return (
              <tr key={`${h.strategy}-${h.decision_date}`} title={h.tickers.map(tick).join(', ')}>
                <td>{fmtDate(h.decision_date)}{h.backfilled && <span className="hl-adv-hint" title="Program nie był uruchomiony w dniu zmiany - typy policzono z danych do tamtego dnia."> · dorobione</span>}</td>
                <td><span style={{ color: COLORS[h.strategy], fontWeight: 700 }}>{h.strategy}</span></td>
                <td><StatusBadge status={h.status} /></td>
                <td style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                  {h.entry_date ? `${fmtDate(h.entry_date)} → ${h.status === 'rozliczony' ? fmtDate(h.end_date) : 'trwa'}` : '—'}
                </td>
                <td className="hl-num" style={{ textAlign: 'right', color: tone(h.return) }}>{pct(h.return)}</td>
                <td className="hl-num" style={{ textAlign: 'right', color: 'var(--text-muted)' }}>{pct(h.hold_return)}</td>
                <td className="hl-num" style={{ textAlign: 'right', color: tone(diff), fontWeight: 600 }}>{pct(diff)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function VaultBox({ vault }) {
  if (!vault?.results) return null
  return (
    <div className="hl-panel" style={{ padding: '16px 20px', marginBottom: 16 }}>
      <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>Dla porównania: wynik testu na historii (sejf)</h3>
      <div className="hl-adv-hint" style={{ margin: '0 0 10px' }}>
        Rok {fmtDate(vault.period?.[0])} – {fmtDate(vault.period?.[1])}, sprawdzony raz, uczciwie, przed startem gry.
        Ten rok był wyjątkowo dobry dla takich strategii - w słabym roku mogą stracić nawet 30–50%.
      </div>
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13.5 }}>
        {['A1', 'A2', 'A3'].map((k) => vault.results[k] && (
          <div key={k}>
            <span style={{ color: COLORS[k], fontWeight: 700 }}>{k}</span>{' '}
            <span className="hl-num" style={{ color: tone(vault.results[k].total) }}>{pct(vault.results[k].total, 0)}</span>
          </div>
        ))}
        <div>
          <span style={{ color: COLORS.hold, fontWeight: 700 }}>trzymanie</span>{' '}
          <span className="hl-num">{pct(vault.hold_total, 0)}</span>
        </div>
      </div>
    </div>
  )
}

export default function QuantPulsePanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/lab/paper`)
      if (!res.ok) throw new Error('Nie udało się wczytać gry na papierze.')
      const d = await res.json()
      setData(d)
      setError('')
      return d
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? 'Backend nie odpowiada - czy jest uruchomiony?' : err.message)
      return null
    }
  }, [])

  useEffect(() => { load() }, [load])

  // W trakcie rozliczania sprawdzamy co 4 s, czy już skończone - potem raz odświeżamy całość.
  const running = !!data?.running
  useEffect(() => {
    if (!running) return
    const id = setInterval(load, 4000)
    return () => clearInterval(id)
  }, [running, load])

  const runNow = async () => {
    setStarting(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/lab/paper/run`, { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Nie udało się uruchomić rozliczania.')
      setData((prev) => (prev ? { ...prev, running: true } : prev))
    } catch (err) {
      setError(err.message)
    } finally {
      setStarting(false)
    }
  }

  if (!data) {
    return error
      ? <div className="hl-adv-error">{error}</div>
      : <div className="hl-panel" style={{ padding: 28, color: 'var(--text-dim)' }}>Wczytuję grę na papierze…</div>
  }

  const rep = data.report
  const last = data.last_run

  return (
    <div>
      <div className="hl-panel" style={{ padding: '20px 24px', marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0, flex: '1 1 320px' }}>
            <h2 style={{ margin: 0, fontSize: 17 }}>Gra na papierze</h2>
            <div className="hl-adv-hint" style={{ margin: '4px 0 0' }}>
              Trzy zamrożone strategie wybierają po 10 spółek z GPW, skład zmienia się co {rep?.every_sessions || 20} sesji (ok. miesiąc).
              Wynik porównujemy ze zwykłym trzymaniem wszystkich spółek po równo. Koszty (0,10% od każdej transakcji) są wliczone.
            </div>
            {data.game && (
              <div className="hl-adv-hint" style={{ margin: '8px 0 0' }}>
                {rep?.started && <>Start gry: {fmtDate(rep.started)} · </>}
                {rep?.last_session && <>ostatnia sesja w danych: {fmtDate(rep.last_session)} · </>}
                {rep?.sessions_to_next != null && <>następna zmiana składu za {rep.sessions_to_next} sesji · </>}
                rozliczanie automatyczne {data.auto.when}
                {data.last_ok && <> · ostatnio {fmtDateTime(data.last_ok)}</>}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
            <button className="hl-btn hl-btn-primary" onClick={runNow} disabled={!data.game || running || starting}
              title={!data.game ? 'Gra jest prowadzona na komputerze stacjonarnym' : 'To samo co python -m lab.paper run'}>
              {running ? 'Trwa rozliczanie… (kilka minut)' : 'Rozlicz teraz'}
            </button>
            {running && <span className="hl-adv-hint" style={{ margin: 0 }}>Możesz w tym czasie korzystać z reszty aplikacji.</span>}
          </div>
        </div>
      </div>

      {error && <div className="hl-adv-error">{error}</div>}
      {data.error && !running && (
        <div className="hl-adv-error">Ostatnie rozliczanie się nie udało: {data.error}. Poprzedni wynik zostaje - spróbuj ponownie później.</div>
      )}

      {!data.game && (
        <div className="hl-panel" style={{ padding: 24, marginBottom: 16, color: 'var(--text-muted)', fontSize: 14, lineHeight: 1.6 }}>
          Na tym komputerze nie ma gry na papierze - jest prowadzona na <strong>komputerze stacjonarnym</strong>.
          Rozliczanie jest tu wyłączone, żeby przypadkiem nie założyć drugiej, osobnej gry.
          {rep && ' Poniżej ostatni zapisany raport.'}
        </div>
      )}

      {data.game && <LiveCheck />}

      {data.game && !rep && (
        <div className="hl-panel" style={{ padding: 24, marginBottom: 16, color: 'var(--text-muted)', fontSize: 14 }}>
          Nie ma jeszcze raportu dla aplikacji. Kliknij „Rozlicz teraz" albo poczekaj na rozliczenie automatyczne.
        </div>
      )}

      {rep && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, marginBottom: 16 }}>
            {rep.strategies.map((s) => <StrategyCard key={s.key} s={s} startValue={rep.start_value} />)}
          </div>

          <div className="hl-panel" style={{ padding: '16px 20px', marginBottom: 16 }}>
            <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>Ile byłoby z {zl(rep.start_value)}</h3>
            <div className="hl-adv-hint" style={{ margin: '0 0 10px', display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              {['A1', 'A2', 'A3'].map((k) => <span key={k}><span style={{ color: COLORS[k] }}>━</span> {k} {SHORT[k]}</span>)}
              <span><span style={{ color: COLORS.hold }}>┅</span> zwykłe trzymanie</span>
            </div>
            <EquityChart curve={rep.curve || []} />
          </div>

          <div className="hl-panel" style={{ padding: '16px 20px', marginBottom: 16 }}>
            <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Bieżący skład</h3>
            <CurrentPicks strategies={rep.strategies} />
            <div className="hl-adv-hint">
              Wynik każdej spółki liczony od kupna (zamknięcie sesji po wskazaniu) do ostatniej sesji w danych.
              Ceny uwzględniają dywidendy.
            </div>
          </div>

          <div className="hl-panel" style={{ padding: '16px 20px', marginBottom: 16 }}>
            <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Historia miesięcy</h3>
            <History history={rep.history} />
          </div>
        </>
      )}

      <VaultBox vault={data.vault} />

      {last?.log?.length > 0 && (
        <details className="hl-panel" style={{ padding: '12px 20px', marginBottom: 16 }}>
          <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--text-muted)' }}>
            Szczegóły ostatniego rozliczenia ({fmtDateTime(last.finished)}, {last.trigger}{last.ok ? '' : ', błąd'})
          </summary>
          <pre style={{ fontSize: 11.5, color: 'var(--text-dim)', whiteSpace: 'pre-wrap', margin: '10px 0 0' }}>
            {last.log.join('\n')}
          </pre>
        </details>
      )}

      <div className="hl-adv-hint" style={{ textAlign: 'center', marginBottom: 8 }}>
        To tylko zapis na papierze - nic nie jest kupowane, laboratorium nie łączy się z XTB.
        Wyniki z przeszłości nie gwarantują przyszłych. To nie jest porada inwestycyjna.
      </div>
    </div>
  )
}
