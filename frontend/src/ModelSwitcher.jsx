import { useState, useEffect, useRef, useCallback } from 'react'

const API = 'http://127.0.0.1:8000'

const MODES = [
  { key: 'mocny', label: 'Mocny', hint: 'Zaczyna od najnowszego Flash. Najlepsza jakość analiz, ale każdy Flash ma tylko 20 zapytań na dobę.' },
  { key: 'oszczedny', label: 'Oszczędny', hint: 'Zaczyna od modeli Flash Lite — 500 zapytań na dobę zamiast 20. Odpowiedzi krótsze i płytsze, ale limit praktycznie nie do wyczerpania.' },
  { key: 'reczny', label: 'Ręczny', hint: 'Wymuszasz konkretny model. Jeśli wyczerpie limit, aplikacja i tak zejdzie na zapasowy — inaczej analiza po prostu by nie ruszyła.' },
]

/** "6h 12min" / "45 s" — ile jeszcze do końca kwarantanny. */
function formatLeft(seconds) {
  if (!seconds || seconds <= 0) return ''
  if (seconds < 90) return `${Math.round(seconds)} s`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h > 0) return m > 0 ? `${h}h ${m}min` : `${h}h`
  return `${m} min`
}

export default function ModelSwitcher({ mini }) {
  const [status, setStatus] = useState(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const boxRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/ai/status`)
      if (!r.ok) throw new Error(`Backend zwrócił ${r.status}`)
      setStatus(await r.json())
      setErr('')
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  useEffect(() => {
    load()
    // Odświeżamy w tle, żeby kwarantanna po wyczerpanym limicie sama znikała
    // z listy, gdy minie — bez klikania i bez przeładowania strony.
    const t = setInterval(load, 20000)
    return () => clearInterval(t)
  }, [load])

  // Klik poza panelem go zamyka
  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const send = async (path, body) => {
    setBusy(true)
    try {
      const r = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      })
      if (!r.ok) throw new Error(`Backend zwrócił ${r.status}`)
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const setMode = (mode) => send('/api/ai/config', { mode })
  const pickModel = (id) => send('/api/ai/config', { mode: 'reczny', manual_model: id })
  const clearCooldowns = () => send('/api/ai/clear-cooldowns')

  // Kolor kropki: zielony = jest z czego strzelać, bursztyn = część spalona,
  // czerwony = wszystko na kwarantannie, szary = brak kontaktu z backendem.
  const models = status?.models || []
  const free = models.filter((m) => m.available).length
  const dot = !status
    ? 'var(--text-dim)'
    : !status.any_available
      ? 'var(--down)'
      : free < models.length
        ? 'var(--warn)'
        : 'var(--accent-bright)'

  const activeLabel = status?.active_label || status?.active_model || (err ? 'brak kontaktu' : '…')
  const shortLabel = activeLabel.replace(/^Gemini\s*/i, '')

  return (
    <div className="hl-ms" ref={boxRef}>
      <button
        className={`hl-ms-trigger ${mini ? 'hl-ms-trigger-mini' : ''}`}
        onClick={() => setOpen((v) => !v)}
        title={`Model: ${activeLabel}${status ? ` · wolnych ${free}/${models.length}` : ''}`}
      >
        <span className="hl-ms-dot" style={{ background: dot, boxShadow: `0 0 8px ${dot}` }} />
        {!mini && <span className="hl-ms-name">{shortLabel}</span>}
        {!mini && <span className="hl-ms-caret">▴</span>}
      </button>

      {open && (
        <div className="hl-ms-pop">
          <div className="hl-ms-head">
            <span>Model analiz</span>
            {status && (
              <span className="hl-ms-count">
                {status.total_used_today} zapytań dziś
              </span>
            )}
          </div>

          {err && <div className="hl-ms-err">Nie mogę pobrać statusu: {err}</div>}

          {status?.catalog_error && (
            <div className="hl-ms-err">Lista modeli z Google: {status.catalog_error}</div>
          )}

          <div className="hl-ms-modes">
            {MODES.map((m) => (
              <button
                key={m.key}
                disabled={busy}
                title={m.hint}
                onClick={() => setMode(m.key)}
                className={`hl-ms-mode ${status?.mode === m.key ? 'hl-ms-mode-on' : ''}`}
              >
                {m.label}
              </button>
            ))}
          </div>

          <div className="hl-ms-hint">
            {MODES.find((m) => m.key === status?.mode)?.hint}
          </div>

          {status?.last_fallback && (
            <div className="hl-ms-fallback">
              Ostatnio zszedłem z <strong>{status.last_fallback.wanted}</strong> na{' '}
              <strong>{status.last_fallback.used}</strong> — limit wyczerpany.
            </div>
          )}

          <div className="hl-ms-list">
            {models.map((m) => (
              <button
                key={m.id}
                disabled={busy}
                onClick={() => pickModel(m.id)}
                className={`hl-ms-row ${status?.manual_model === m.id && status?.mode === 'reczny' ? 'hl-ms-row-on' : ''}`}
                title={m.available ? 'Kliknij, żeby wymusić ten model' : `Limit wyczerpany, wraca za ${formatLeft(m.cooldown_seconds)}`}
              >
                <span
                  className="hl-ms-dot"
                  style={{
                    background: m.available ? 'var(--accent-bright)' : 'var(--down)',
                    opacity: m.available ? 1 : 0.7,
                  }}
                />
                <span className="hl-ms-row-name">
                  {m.label.replace(/^Gemini\s*/i, '')}
                  {status?.active_model === m.id && <em className="hl-ms-tag">w użyciu</em>}
                </span>
                <span className="hl-ms-row-meta">
                  {m.available
                    ? (m.used_today > 0 ? `${m.used_today}×` : '')
                    : formatLeft(m.cooldown_seconds)}
                </span>
              </button>
            ))}
            {models.length === 0 && !err && (
              <div className="hl-ms-hint" style={{ padding: '8px 2px' }}>Wczytuję listę modeli…</div>
            )}
          </div>

          <button className="hl-ms-reset" disabled={busy} onClick={clearCooldowns}>
            Zdejmij kwarantannę ze wszystkich
          </button>
          <div className="hl-ms-foot">
            Limity darmowego klucza resetują się o północy czasu pacyficznego
            (ok. 9:00 u nas). Przycisk wyżej przydaje się, gdy reset przyszedł
            wcześniej, niż aplikacja oszacowała.
          </div>
        </div>
      )}
    </div>
  )
}
