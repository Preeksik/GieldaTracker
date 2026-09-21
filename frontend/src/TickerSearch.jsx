import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'

const API = 'http://127.0.0.1:8000'

/**
 * Pole wyszukiwania spółki po NAZWIE, z podpowiedziami jak w pasku adresu przeglądarki.
 *
 * Zasada, której się trzyma: nigdy nie blokuje. Gdy Yahoo nie odpowiada albo spółki
 * nie ma w podpowiedziach, wpisany ręcznie ticker wciąż działa - dokładnie tak,
 * jak działało przed dodaniem wyszukiwarki.
 *
 * Props:
 *   value, onChange(text)      - kontrolowana wartość (sam ticker)
 *   onPick(entry)              - wybrano podpowiedź {ticker, name, exchange, type}
 *   onEnter()                  - Enter przy zamkniętej liście (np. "analizuj")
 *   placeholder, style, autoFocus, verify
 */
export default function TickerSearch({
  value,
  onChange,
  onPick,
  onEnter,
  placeholder = 'Nazwa spółki albo ticker (np. Orlen, CDR.WA)',
  style,
  autoFocus = false,
  verify = true,
}) {
  const [items, setItems] = useState([])
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [warning, setWarning] = useState('')
  const [checked, setChecked] = useState(null)   // wynik sprawdzenia po wyborze

  const boxRef = useRef(null)
  const inputRef = useRef(null)
  const popRef = useRef(null)
  const abortRef = useRef(null)
  const skipNextRef = useRef(false)              // po wyborze nie szukamy od nowa
  const [pos, setPos] = useState(null)           // położenie listy na ekranie

  // Lista jest renderowana przez portal bezpośrednio w <body>, a nie wewnątrz pola.
  // Panele aplikacji mają backdrop-filter i z-index, co tworzy osobne konteksty warstw:
  // lista zamknięta w panelu formularza NIE MOGŁA wyjść ponad panel wykresu leżący
  // niżej, niezależnie od własnego z-indexu - widać było tylko pierwszy wiersz.
  // Dochodzą do tego panele z overflow:hidden, które zwyczajnie ją obcinały.
  // Dlatego liczymy pozycję pola na ekranie i kładziemy listę na samym wierzchu.
  const place = useCallback(() => {
    const el = inputRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const below = window.innerHeight - r.bottom - 12
    const above = r.top - 12
    // Gdy pod polem brakuje miejsca (pole na dole ekranu), otwieramy listę do góry.
    const up = below < 220 && above > below
    setPos({
      left: Math.max(8, Math.min(r.left, window.innerWidth - Math.max(r.width, 320) - 8)),
      width: Math.max(r.width, 320),
      top: up ? undefined : r.bottom + 6,
      bottom: up ? window.innerHeight - r.top + 6 : undefined,
      maxHeight: Math.max(160, Math.min(360, up ? above : below)),
    })
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    place()
    // capture: true - łapiemy przewijanie KAŻDEGO kontenera, nie tylko okna
    // (panel treści i pasek boczny przewijają się niezależnie).
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, place, items.length])

  // --- pobieranie podpowiedzi, z opóźnieniem i anulowaniem starych zapytań ---
  useEffect(() => {
    const q = (value || '').trim()

    if (skipNextRef.current) {
      skipNextRef.current = false
      return
    }
    if (q.length < 2) {
      setItems([])
      setWarning('')
      return
    }

    const timer = setTimeout(async () => {
      // Bez anulowania starsze zapytanie potrafi wrócić PO nowszym i podmienić
      // listę na wyniki dla poprzednio wpisanego tekstu.
      if (abortRef.current) abortRef.current.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl

      setLoading(true)
      try {
        const r = await fetch(`${API}/api/search/tickers?q=${encodeURIComponent(q)}&limit=8`,
          { signal: ctrl.signal })
        if (!r.ok) throw new Error(`Backend zwrócił ${r.status}`)
        const data = await r.json()
        setItems(data.results || [])
        setWarning(data.warning || '')
        setCursor(-1)
        setOpen(true)
      } catch (e) {
        if (e.name !== 'AbortError') {
          setItems([])
          setWarning('Nie mogę pobrać podpowiedzi — wpisz ticker ręcznie.')
        }
      } finally {
        setLoading(false)
      }
    }, 220)

    return () => clearTimeout(timer)
  }, [value])

  // Strzałki przewijają listę razem z podświetleniem - przy niskim oknie lista
  // ma ograniczoną wysokość i podświetlony wiersz uciekałby poza widok.
  useEffect(() => {
    if (cursor < 0 || !popRef.current) return
    const row = popRef.current.querySelectorAll('.hl-ts-row')[cursor]
    if (row) row.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  // Klik poza polem zamyka listę. Lista siedzi teraz w <body> (portal), więc
  // "wewnątrz" oznacza pole ALBO listę - inaczej klik w podpowiedź zamykałby ją,
  // zanim zdąży się wybrać.
  useEffect(() => {
    const onDown = (e) => {
      const inBox = boxRef.current && boxRef.current.contains(e.target)
      const inPop = popRef.current && popRef.current.contains(e.target)
      if (!inBox && !inPop) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const choose = useCallback(async (entry) => {
    skipNextRef.current = true
    onChange(entry.ticker)
    setOpen(false)
    setItems([])
    setCursor(-1)
    if (onPick) onPick(entry)

    if (!verify) return
    setChecked({ loading: true, ticker: entry.ticker })
    try {
      const r = await fetch(`${API}/api/search/resolve?ticker=${encodeURIComponent(entry.ticker)}`)
      const data = await r.json()
      setChecked({ ...data, loading: false })
    } catch {
      setChecked(null)
    }
  }, [onChange, onPick, verify])

  const onKeyDown = (e) => {
    if (!open || items.length === 0) {
      if (e.key === 'Enter' && onEnter) onEnter()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((c) => (c + 1) % items.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((c) => (c <= 0 ? items.length - 1 : c - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (cursor >= 0) choose(items[cursor])
      else if (onEnter) onEnter()
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="hl-ts" ref={boxRef} style={style}>
      <input
        ref={inputRef}
        className="hl-input hl-ts-input"
        value={value || ''}
        autoFocus={autoFocus}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value)
          setChecked(null)
        }}
        onFocus={() => items.length > 0 && setOpen(true)}
        onKeyDown={onKeyDown}
        autoComplete="off"
        spellCheck={false}
      />
      {loading && <span className="hl-ts-spin" />}

      {open && items.length > 0 && pos && createPortal(
        <div
          ref={popRef}
          className="hl-ts-pop"
          style={{
            left: pos.left,
            width: pos.width,
            top: pos.top,
            bottom: pos.bottom,
            maxHeight: pos.maxHeight,
          }}
        >
          {items.map((it, i) => (
            <button
              key={it.ticker}
              type="button"
              className={`hl-ts-row ${i === cursor ? 'hl-ts-row-on' : ''}`}
              onMouseEnter={() => setCursor(i)}
              onMouseDown={(e) => e.preventDefault()}   // nie gub focusu przed kliknięciem
              onClick={() => choose(it)}
            >
              <span className="hl-ts-tick">{it.ticker}</span>
              <span className="hl-ts-name">{it.name}</span>
              {it.owned && <span className="hl-ts-owned">masz</span>}
              {it.exchange && <span className="hl-ts-exch">{it.exchange}</span>}
            </button>
          ))}
          {warning && <div className="hl-ts-warn">{warning}</div>}
        </div>,
        document.body
      )}

      {!open && warning && <div className="hl-ts-warn hl-ts-warn-inline">{warning}</div>}

      {checked && (
        <div className={`hl-ts-check ${checked.ok === false ? 'hl-ts-check-bad' : ''}`}>
          {checked.loading
            ? 'Sprawdzam dane…'
            : checked.ok
              ? `✓ ${checked.name} · ${checked.price} ${checked.currency}`
              : `✗ ${checked.detail}`}
        </div>
      )}
    </div>
  )
}