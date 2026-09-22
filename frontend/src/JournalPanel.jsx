import { useState, useEffect, useCallback } from 'react'
import MarkdownView from './MarkdownView'

const API = 'http://127.0.0.1:8000'

const OUTCOMES = [
  { key: 'trafiona', label: 'Trafiona', cls: 'hl-jr-o-good' },
  { key: 'czesciowo', label: 'Częściowo', cls: 'hl-jr-o-mid' },
  { key: 'chybiona', label: 'Chybiona', cls: 'hl-jr-o-bad' },
]
const HORIZON = { krotki: 'krótki horyzont', sredni: 'średni horyzont', dlugi: 'długi horyzont' }

function fullDate(iso) {
  return new Date(iso).toLocaleString('pl-PL', {
    day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function ago(days) {
  if (days <= 0) return 'dziś'
  if (days === 1) return 'wczoraj'
  if (days < 30) return `${days} dni temu`
  const m = Math.round(days / 30)
  return m === 1 ? 'miesiąc temu' : `${m} mies. temu`
}

const pct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
const cls = (v) => (v == null ? '' : v > 0 ? 'hl-up' : v < 0 ? 'hl-down' : '')

async function api(path, opts) {
  const r = await fetch(`${API}${path}`, opts)
  if (!r.ok) {
    let d = `Backend zwrócił ${r.status}`
    try { const b = await r.json(); if (b?.detail) d = b.detail } catch { /* brak JSON */ }
    throw new Error(d)
  }
  return r.json()
}

function Entry({ e, sources, onChange }) {
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState(e.note || '')
  const r = e.result || {}
  const ref = r.benchmarks?.[r.reference]

  const patch = async (body) => {
    await api(`/api/journal/${e.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    onChange()
  }

  const remove = async () => {
    if (!window.confirm('Usunąć tę poradę z dziennika? Tego nie da się cofnąć.')) return
    await api(`/api/journal/${e.id}`, { method: 'DELETE' })
    onChange()
  }

  return (
    <div className={`hl-panel hl-jr-entry ${e.pinned ? 'hl-jr-pinned' : ''}`}>
      <div className="hl-jr-head" onClick={() => setOpen((v) => !v)}>
        <div className="hl-jr-date">
          <div className="hl-jr-date-main">{fullDate(e.created_at)}</div>
          <div className="hl-jr-date-sub">{ago(r.days ?? 0)}</div>
        </div>

        <div className="hl-jr-main">
          <div className="hl-jr-meta">
            <span className="hl-jr-badge">{sources?.[e.source] || e.source}</span>
            {e.brief?.profile && <span>{e.brief.profile}</span>}
            {!e.brief?.profile && e.brief?.horizon && <span>{HORIZON[e.brief.horizon]}</span>}
            {e.brief?.amount ? <span>{Number(e.brief.amount).toLocaleString('pl-PL')} zł</span> : null}
            {e.model && <span className="hl-jr-model">{e.model}</span>}
            {e.followups?.length > 0 && <span>+{e.followups.length} dopytań</span>}
            {e.pinned && <span className="hl-jr-pin">📌</span>}
          </div>
          <div className="hl-jr-q">{e.question || '(bez pytania)'}</div>

          {r.rows?.length > 0 && (
            <div className="hl-jr-tickers">
              {r.rows.map((t) => (
                <span key={t.ticker} className="hl-jr-tk" title={`${t.name || t.ticker}: ${t.price} → ${t.now ?? '?'} ${t.currency || ''}`}>
                  <b>{t.ticker}</b>
                  <span className={cls(t.change_pct)}>{pct(t.change_pct)}</span>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="hl-jr-score">
          {r.avg_change_pct != null ? (
            <>
              <div className={`hl-jr-avg ${cls(r.avg_change_pct)}`}>{pct(r.avg_change_pct)}</div>
              {r.vs_market_pct != null && (
                <div className="hl-jr-vs">
                  <span className={cls(r.vs_market_pct)}>{r.vs_market_pct > 0 ? '+' : ''}{r.vs_market_pct.toFixed(2)} pp</span> vs {ref?.label}
                </div>
              )}
              <div className={`hl-jr-status ${r.mature ? 'hl-jr-ready' : ''}`}>
                {r.mature ? 'do oceny' : `za wcześnie · ocena za ${Math.max(1, Math.ceil(r.horizon_days / 4) - r.days)} dni`}
              </div>
            </>
          ) : (
            <div className="hl-jr-status">bez spółek do śledzenia</div>
          )}
          {e.outcome && (
            <div className={`hl-jr-outcome ${OUTCOMES.find((o) => o.key === e.outcome)?.cls}`}>
              {OUTCOMES.find((o) => o.key === e.outcome)?.label}
            </div>
          )}
        </div>
      </div>

      {open && (
        <div className="hl-jr-body hl-fade-up">
          {r.rows?.length > 0 && (
            <div style={{ overflowX: 'auto', marginBottom: 16 }}>
              <table className="hl-table">
                <thead>
                  <tr>
                    <th>Spółka</th>
                    <th style={{ textAlign: 'right' }}>W dniu porady</th>
                    <th style={{ textAlign: 'right' }}>Dziś</th>
                    <th style={{ textAlign: 'right' }}>Zmiana</th>
                  </tr>
                </thead>
                <tbody>
                  {r.rows.map((t) => (
                    <tr key={t.ticker}>
                      <td><span className="hl-adv-vtick" style={{ minWidth: 0, marginRight: 8 }}>{t.ticker}</span>{t.name}
                        {t.added_at && <span className="hl-adv-vmeta"> · dodana w dopytaniu {new Date(t.added_at).toLocaleDateString('pl-PL')}</span>}
                      </td>
                      <td className="hl-num" style={{ textAlign: 'right' }}>{t.price} {t.currency}</td>
                      <td className="hl-num" style={{ textAlign: 'right' }}>{t.now ?? '—'} {t.now != null ? t.currency : ''}</td>
                      <td className={`hl-num ${cls(t.change_pct)}`} style={{ textAlign: 'right' }}>{pct(t.change_pct)}</td>
                    </tr>
                  ))}
                  {Object.values(r.benchmarks || {}).map((b) => (
                    <tr key={b.label} className="hl-jr-bench">
                      <td>{b.label} <span className="hl-adv-vmeta">· rynek</span></td>
                      <td className="hl-num" style={{ textAlign: 'right' }}>{b.then ?? '—'}</td>
                      <td className="hl-num" style={{ textAlign: 'right' }}>{b.now ?? '—'}</td>
                      <td className={`hl-num ${cls(b.change_pct)}`} style={{ textAlign: 'right' }}>{pct(b.change_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <MarkdownView>{e.answer}</MarkdownView>

          {e.followups?.map((f, i) => (
            <div key={i} className="hl-adv-followitem" style={{ marginTop: 14 }}>
              <div className="hl-adv-followq">
                {f.question} <span className="hl-adv-vmeta">· {fullDate(f.at)}</span>
              </div>
              <MarkdownView>{f.answer}</MarkdownView>
            </div>
          ))}

          <div className="hl-jr-actions">
            <div className="hl-adv-field" style={{ flex: 1, minWidth: 240 }}>
              <span className="hl-adv-label">Twoja notatka</span>
              <textarea
                className="hl-input"
                rows={2}
                value={note}
                onChange={(ev) => setNote(ev.target.value)}
                onBlur={() => note !== (e.note || '') && patch({ note })}
                placeholder="np. kupiłem Ryvu po 40,20, sprzedałem po 46 — wyszło, ale przed raportem"
                style={{ resize: 'vertical' }}
              />
            </div>
            <div className="hl-adv-field">
              <span className="hl-adv-label">Jak wyszło?</span>
              <div className="hl-adv-chips">
                {OUTCOMES.map((o) => (
                  <button key={o.key} type="button"
                    className={`hl-adv-chip ${e.outcome === o.key ? 'hl-adv-chip-on' : ''}`}
                    onClick={() => patch({ outcome: e.outcome === o.key ? '' : o.key })}>
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="hl-jr-foot">
            <button className="hl-btn hl-btn-sm" onClick={() => patch({ pinned: !e.pinned })}>
              {e.pinned ? 'Odepnij' : '📌 Przypnij'}
            </button>
            <button className="hl-btn hl-btn-sm hl-jr-del" onClick={remove}>Usuń</button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function JournalPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ q, source })
      setData(await api(`/api/journal?${params}`))
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [q, source])

  useEffect(() => {
    const t = setTimeout(load, 250)
    return () => clearTimeout(t)
  }, [load])

  const s = data?.summary

  return (
    <div>
      <div className="hl-panel" style={{ padding: '22px 24px', marginBottom: 18 }}>
        <h2 style={{ margin: '0 0 6px', fontSize: 17 }}>Dziennik porad</h2>
        <p className="hl-adv-hint" style={{ margin: '0 0 16px' }}>
          Każda porada z Doradcy i analiza spółki zapisuje się sama — z datą i cenami z tamtej
          chwili. Automatycznie, bo przy ręcznym zapisie zostawałyby głównie trafione porady
          i bilans byłby fałszywie optymistyczny. Niepotrzebne usuniesz ręcznie.
        </p>

        {s && s.total > 0 && (
          <div className="hl-jr-summary">
            <div><b>{s.total}</b><span>porad</span></div>
            <div><b>{s.mature}</b><span>do oceny</span></div>
            <div>
              <b className={cls(s.avg_vs_market_pct)}>
                {s.avg_vs_market_pct != null ? `${s.avg_vs_market_pct > 0 ? '+' : ''}${s.avg_vs_market_pct.toFixed(2)} pp` : '—'}
              </b>
              <span>średnio vs rynek</span>
            </div>
            <div><b>{s.mature ? `${s.beat_market}/${s.mature}` : '—'}</b><span>pobiło rynek</span></div>
          </div>
        )}
        {s && s.mature > 0 && s.mature < 10 && (
          <div className="hl-adv-hint" style={{ marginTop: 10 }}>
            Tylko {s.mature} {s.mature === 1 ? 'porada dojrzała' : 'porad dojrzało'} do oceny — przy tak małej
            próbie wynik to w dużej mierze przypadek. Sensowny obraz daje dopiero kilkanaście–kilkadziesiąt.
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 16, flexWrap: 'wrap' }}>
          <input className="hl-input" style={{ flex: 1, minWidth: 220 }} value={q}
            onChange={(e) => setQ(e.target.value)} placeholder="Szukaj w pytaniach, odpowiedziach i spółkach…" />
          <select className="hl-input" value={source} onChange={(e) => setSource(e.target.value)} style={{ width: 190 }}>
            <option value="">Wszystkie źródła</option>
            {Object.entries(data?.sources || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>

      {error && <div className="hl-adv-error">{error}</div>}
      {loading && !data && <div className="hl-adv-hint">Wczytuję dziennik i bieżące ceny…</div>}

      {data && data.entries.length === 0 && (
        <div className="hl-panel" style={{ padding: 28, textAlign: 'center', color: 'var(--text-dim)' }}>
          {q || source ? 'Nic nie pasuje do wyszukiwania.' : 'Dziennik jest pusty. Zadaj pytanie w Doradcy albo przeanalizuj spółkę — porada zapisze się tu sama.'}
        </div>
      )}

      {data?.entries.map((e) => (
        <Entry key={e.id} e={e} sources={data.sources} onChange={load} />
      ))}
    </div>
  )
}
