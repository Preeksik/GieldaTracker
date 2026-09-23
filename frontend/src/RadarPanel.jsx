import { useState, useEffect, useCallback } from 'react'
import MarkdownView from './MarkdownView'
import PortfolioNews from './PortfolioNews'

const API = 'http://127.0.0.1:8000'

const MARKETS = [
  { key: 'all', label: 'Wszystko' },
  { key: 'PL', label: '🇵🇱 Polska' },
  { key: 'US', label: '🇺🇸 USA' },
]
const KINDS = [
  { key: 'all', label: 'Wszystkie sygnały' },
  { key: 'insider', label: 'Insiderzy' },
  { key: 'espi', label: 'Komunikaty' },
  { key: 'news', label: 'Media' },
  { key: 'social', label: 'Reddit' },
]
const TYPE_LABEL = {
  insider_us: 'Insider USA', insider_pl: 'Insider (MAR)', espi: 'Komunikat', news: 'Media', social: 'Reddit',
}

// Poniżej tego progu są rutynowe komunikaty (asymilacja akcji, pojedyncza wzmianka) -
// chowamy je pod przyciskiem, żeby na liście zostały tylko rzeczy naprawdę gorące.
// 10 pkt = np. świeża rekomendacja, znacząca umowa albo powiadomienie MAR (14).
const MIN_SCORE = 10

function ago(iso) {
  if (!iso) return ''
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (m < 60) return `${m} min temu`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} godz. temu`
  const d = Math.round(h / 24)
  return d === 1 ? 'wczoraj' : `${d} dni temu`
}

async function api(path, opts) {
  const r = await fetch(`${API}${path}`, opts)
  if (!r.ok) {
    let d = `Backend zwrócił ${r.status}`
    try { const b = await r.json(); if (b?.detail) d = b.detail } catch { /* brak JSON */ }
    throw new Error(d)
  }
  return r.json()
}

function SourceStatus({ status }) {
  const entries = Object.entries(status || {})
  if (!entries.length) return null
  return (
    <div className="hl-rd-sources">
      {entries.map(([k, s]) => (
        <span
          key={k}
          className={`hl-rd-src ${s.ok ? 'hl-rd-src-ok' : s.note ? 'hl-rd-src-off' : 'hl-rd-src-bad'}`}
          title={s.error || s.note || `nowych sygnałów: ${s.new ?? 0}`}
        >
          {s.ok ? '●' : s.note ? '○' : '✕'} {s.label}
        </span>
      ))}
    </div>
  )
}

function HotCard({ g, rank, maxScore, watched, onWatch }) {
  const [open, setOpen] = useState(false)
  const [ai, setAi] = useState(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')
  const m = g.market_data

  const explain = async () => {
    setAiLoading(true)
    setAiError('')
    try {
      const d = await api('/api/radar/explain', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: g.key }),
      })
      setAi(d.explanation)
    } catch (e) {
      setAiError(e.message)
    } finally {
      setAiLoading(false)
    }
  }

  const shown = open ? g.signals : g.signals.slice(0, 3)
  const heat = Math.max(6, Math.round((g.score / Math.max(maxScore, 1)) * 100))

  return (
    <div className="hl-panel hl-rd-card">
      <div className="hl-rd-rank">
        <div className="hl-rd-rank-n">#{rank}</div>
        <div className="hl-rd-heat" title={`Temperatura: ${g.score} pkt`}>
          <div className="hl-rd-heat-fill" style={{ height: `${heat}%` }} />
        </div>
        <div className="hl-rd-score">{Math.round(g.score)}</div>
      </div>

      <div className="hl-rd-body">
        <div className="hl-rd-title">
          <span className="hl-rd-name">{g.company}</span>
          {g.ticker && <span className="hl-rd-ticker">{g.ticker}</span>}
          <span className="hl-rd-mkt">{g.market === 'PL' ? '🇵🇱' : '🇺🇸'}</span>
          {g.mood !== 'mieszany' && (
            <span className={g.mood === 'pozytywny' ? 'hl-up' : 'hl-down'} title={`Wydźwięk sygnałów: ${g.mood}`}>
              {g.mood === 'pozytywny' ? '▲' : '▼'}
            </span>
          )}
        </div>

        {g.bonuses.length > 0 && (
          <div className="hl-rd-bonuses">
            {g.bonuses.map((b) => (
              <span key={b.label} className="hl-rd-bonus" title={`+${b.points} pkt`}>{b.label}</span>
            ))}
          </div>
        )}

        <ul className="hl-rd-signals">
          {shown.map((s) => (
            <li key={s.id}>
              <span className={`hl-rd-type hl-rd-type-${s.type}`}>{TYPE_LABEL[s.type] || s.type}</span>
              <span className="hl-rd-slabel">{s.label}:</span>{' '}
              {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}
              <span className="hl-rd-meta">
                {' '}· {s.source}{s.also_in?.length ? ` (+ ${s.also_in.join(', ')})` : ''} · {ago(s.date)}
              </span>
            </li>
          ))}
        </ul>
        {g.signal_count > 3 && (
          <button className="hl-rd-more" onClick={() => setOpen((v) => !v)}>
            {open ? 'Zwiń' : `Pokaż wszystkie sygnały (${g.signal_count})`}
          </button>
        )}

        {ai && <div className="hl-rd-ai hl-fade-up"><MarkdownView>{ai}</MarkdownView></div>}
        {aiError && <div className="hl-adv-error" style={{ marginTop: 10 }}>{aiError}</div>}
      </div>

      <div className="hl-rd-side">
        {m ? (
          <div className="hl-rd-market">
            <div>
              <span>5 sesji</span>
              <b className={m.ret_5d > 0 ? 'hl-up' : m.ret_5d < 0 ? 'hl-down' : ''}>
                {m.ret_5d != null ? `${m.ret_5d > 0 ? '+' : ''}${m.ret_5d.toFixed(1)}%` : '—'}
              </b>
            </div>
            <div>
              <span>wolumen</span>
              <b className={m.vol_ratio >= 2 ? 'hl-rd-hot' : ''}>{m.vol_ratio != null ? `${m.vol_ratio.toFixed(1)}×` : '—'}</b>
            </div>
          </div>
        ) : (
          <div className="hl-rd-nomarket">{g.ticker ? 'notowania w drodze' : 'ticker nieustalony'}</div>
        )}
        {g.earnings && g.earnings_days >= 0 && <div className="hl-rd-earn">raport {g.earnings}</div>}
        <div className="hl-rd-actions">
          {g.ticker && (
            <button className="hl-btn hl-btn-sm" disabled={watched} onClick={() => onWatch(g.ticker)}>
              {watched ? '✓ obserwujesz' : '+ Obserwuj'}
            </button>
          )}
          <button className="hl-btn hl-btn-sm" disabled={aiLoading} onClick={explain}>
            {aiLoading ? 'Myślę…' : ai ? 'Odśwież opis' : 'Dlaczego? (AI)'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Hot() {
  const [market, setMarket] = useState('all')
  const [kind, setKind] = useState('all')
  const [view, setView] = useState('ranking')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [watchlist, setWatchlist] = useState([])
  const [showWeak, setShowWeak] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api(`/api/radar/hot?market=${market}&kind=${kind}`))
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [market, kind])

  useEffect(() => { load() }, [load])

  // Zbieranie idzie w tle co 20 min - odświeżamy widok co minutę, żeby nowe sygnały
  // pojawiały się same, bez klikania.
  useEffect(() => {
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [load])

  useEffect(() => {
    api('/api/watchlist').then((d) => setWatchlist(d.tickers || [])).catch(() => {})
  }, [])

  const refresh = async () => {
    setRefreshing(true)
    try {
      await api('/api/radar/refresh', { method: 'POST' })
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setRefreshing(false)
    }
  }

  const watch = async (ticker) => {
    try {
      const d = await api('/api/watchlist', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker }),
      })
      setWatchlist(d.tickers || [])
    } catch (e) {
      setError(e.message)
    }
  }

  const groups = data?.groups || []
  const maxScore = groups[0]?.score || 1
  const strongCount = groups.filter((g) => g.score >= MIN_SCORE).length
  const weakCount = groups.length - strongCount
  // W cichy dzień nic nie przekracza progu - wtedy pokazujemy wszystko, zamiast pustej listy.
  const visible = showWeak || strongCount === 0 ? groups : groups.slice(0, strongCount)
  const secOff = data?.status?.sec_form4 && !data.status.sec_form4.ok && data.status.sec_form4.note

  return (
    <div>
      <div className="hl-panel" style={{ padding: '20px 24px', marginBottom: 16 }}>
        <div className="hl-rd-head">
          <div>
            <h2 style={{ margin: 0, fontSize: 17 }}>Co się teraz grzeje</h2>
            <div className="hl-adv-hint" style={{ margin: '4px 0 0' }}>
              {data?.last_run
                ? <>Zebrane {ago(data.last_run)} · {data.total_signals} sygnałów z ostatnich 14 dni · automatycznie co {data.every_minutes} min</>
                : 'Pierwsze zbieranie rusza ok. minuty po starcie backendu — albo kliknij Odśwież.'}
            </div>
          </div>
          <button className="hl-btn hl-btn-primary" onClick={refresh} disabled={refreshing}>
            {refreshing ? 'Zbieram… (do minuty)' : 'Odśwież teraz'}
          </button>
        </div>

        <SourceStatus status={data?.status} />
        {secOff && (
          <div className="hl-adv-hint" style={{ marginTop: 8 }}>
            Zakupy insiderów w USA są wyłączone: SEC wymaga, żeby program się przedstawił. Dopisz do
            <code> backend\.env</code> linię <code>SEC_USER_AGENT=HossaLab Imię Nazwisko twoj@email.pl</code> i zrestartuj backend.
          </div>
        )}

        <div className="hl-rd-filters">
          <div className="hl-adv-chips">
            {MARKETS.map((m) => (
              <button key={m.key} className={`hl-adv-chip ${market === m.key ? 'hl-adv-chip-on' : ''}`}
                onClick={() => setMarket(m.key)}>{m.label}</button>
            ))}
          </div>
          <div className="hl-adv-chips">
            {KINDS.map((k) => (
              <button key={k.key} className={`hl-adv-chip ${kind === k.key ? 'hl-adv-chip-on' : ''}`}
                onClick={() => setKind(k.key)}>{k.label}</button>
            ))}
          </div>
          <div className="hl-adv-chips" style={{ marginLeft: 'auto' }}>
            <button className={`hl-adv-chip ${view === 'ranking' ? 'hl-adv-chip-on' : ''}`} onClick={() => setView('ranking')}>Ranking spółek</button>
            <button className={`hl-adv-chip ${view === 'stream' ? 'hl-adv-chip-on' : ''}`} onClick={() => setView('stream')}>Strumień newsów</button>
          </div>
        </div>
      </div>

      {error && <div className="hl-adv-error">{error}</div>}

      {view === 'ranking' && (
        <>
          {data && groups.length === 0 && (
            <div className="hl-panel" style={{ padding: 28, textAlign: 'center', color: 'var(--text-dim)' }}>
              {data.total_signals ? 'Nic nie pasuje do wybranych filtrów.' : 'Jeszcze brak sygnałów — kliknij „Odśwież teraz".'}
            </div>
          )}
          {strongCount === 0 && groups.length > 0 && (
            <div className="hl-adv-hint" style={{ margin: '0 0 12px' }}>
              Na razie żadna spółka nie przekroczyła {MIN_SCORE} pkt — poniżej wszystko, co Radar zebrał.
            </div>
          )}
          {visible.map((g, i) => (
            <HotCard key={g.key} g={g} rank={i + 1} maxScore={maxScore}
              watched={watchlist.includes(g.ticker)} onWatch={watch} />
          ))}
          {strongCount > 0 && weakCount > 0 && (
            <button className="hl-btn hl-rd-weak" onClick={() => setShowWeak((v) => !v)}>
              {showWeak
                ? 'Ukryj słabsze sygnały'
                : `Pokaż słabsze sygnały (${weakCount}) — rutynowe komunikaty poniżej ${MIN_SCORE} pkt`}
            </button>
          )}
        </>
      )}

      {view === 'stream' && (
        <div className="hl-panel" style={{ padding: '8px 0' }}>
          {(data?.stream || []).map((s) => (
            <div key={s.id} className="hl-rd-stream-row">
              <span className="hl-rd-stream-time">{ago(s.date)}</span>
              <span className={`hl-rd-type hl-rd-type-${s.type}`}>{TYPE_LABEL[s.type] || s.type}</span>
              <span className="hl-rd-stream-text">
                {s.company && <b>{s.company}{s.ticker ? ` (${s.ticker})` : ''}: </b>}
                {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}
                <span className="hl-rd-meta">
                  {' '}· {s.source}{s.also_in?.length ? ` (+ ${s.also_in.join(', ')})` : ''}
                </span>
              </span>
            </div>
          ))}
          {data && !data.stream?.length && (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-dim)' }}>Brak newsów w strumieniu.</div>
          )}
        </div>
      )}

      <div className="hl-adv-hint" style={{ marginTop: 14 }}>
        Temperatura to suma wag sygnałów, które wygasają z wiekiem, plus premie za klaster insiderów,
        potwierdzenie w kilku źródłach, anomalię wolumenu i bliski raport. To lista rzeczy wartych
        sprawdzenia, a nie rekomendacja zakupu — w chwili, gdy o spółce robi się głośno, spora część
        ruchu bywa już w cenie.
      </div>
    </div>
  )
}

export default function RadarPanel() {
  const [tab, setTab] = useState('hot')
  return (
    <div>
      <div className="hl-adv-chips" style={{ marginBottom: 16 }}>
        <button className={`hl-adv-chip ${tab === 'hot' ? 'hl-adv-chip-on' : ''}`} onClick={() => setTab('hot')}>🔥 Gorące teraz</button>
        <button className={`hl-adv-chip ${tab === 'watch' ? 'hl-adv-chip-on' : ''}`} onClick={() => setTab('watch')}>👁 Moja watchlista</button>
      </div>
      {tab === 'hot' ? <Hot /> : <PortfolioNews />}
    </div>
  )
}
