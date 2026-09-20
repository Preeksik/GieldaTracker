import { useEffect } from 'react'
import { LogoMark, Wordmark } from './Logo'

/**
 * Boczna nawigacja HossaLab.
 *
 * Zakładki są pogrupowane tematycznie, bo przy jedenastu pozycjach płaska lista
 * (czy to pozioma, czy pionowa) zmusza do czytania wszystkiego za każdym razem.
 * Grupy pozwalają skakać wzrokiem od razu do właściwej sekcji.
 *
 * Desktop: pasek na stałe widoczny, opcjonalnie zwijany do samych ikon.
 * Telefon: szuflada wysuwana hamburgerem (klasy w nav.css).
 */

export const NAV_GROUPS = [
  {
    label: 'Portfel',
    items: [
      { key: 'portfel', icon: '◼', label: 'Pozycje' },
      { key: 'sprzedaze', icon: '▼', label: 'Sprzedaże' },
      { key: 'dywidendy', icon: '●', label: 'Dywidendy' },
    ],
  },
  {
    label: 'Analiza',
    items: [
      { key: 'analiza', icon: '◈', label: 'Spółka' },
      { key: 'rekomendacje', icon: '◆', label: 'Portfel AI' },
    ],
  },
  {
    label: 'Rynek',
    items: [
      { key: 'briefing', icon: '☕', label: 'Briefing poranny' },
      { key: 'espi', icon: '📜', label: 'ESPI / EBI' },
      { key: 'newsy', icon: '▲', label: 'Radar' },
      { key: 'alerty', icon: '◉', label: 'Alerty' },
    ],
  },
  {
    label: 'System',
    items: [
      { key: 'automat', icon: '⚙', label: 'Automat' },
      { key: 'dane', icon: '⬢', label: 'Dane' },
    ],
  },
]

/** Płaska lista wszystkich zakładek — do wyszukania tytułu bieżącej sekcji. */
export const ALL_TABS = NAV_GROUPS.flatMap((g) =>
  g.items.map((it) => ({ ...it, group: g.label }))
)

export function findTab(key) {
  return ALL_TABS.find((t) => t.key === key) || ALL_TABS[0]
}

export default function Sidebar({ activeTab, onSelect, mini, onToggleMini, open, onClose }) {
  // Escape zamyka szufladę na telefonie — standard, którego ludzie odruchowo próbują.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // Przy otwartej szufladzie blokujemy przewijanie tła, żeby palec nie scrollował
  // treści pod spodem zamiast menu.
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open])

  return (
    <>
      {open && <div className="hl-nav-backdrop" onClick={onClose} />}

      <aside className={`hl-nav ${mini ? 'hl-nav-mini' : ''} ${open ? 'hl-nav-open' : ''}`}>
        <div className="hl-nav-head">
          <LogoMark size={mini ? 34 : 40} />
          {!mini && (
            <div>
              <Wordmark size={19} />
              <div className="hl-nav-tagline">AI QUANT RESEARCH</div>
            </div>
          )}
        </div>

        <nav className="hl-nav-scroll">
          {NAV_GROUPS.map((group) => (
            <div className="hl-nav-group" key={group.label}>
              <div className="hl-nav-group-label">{group.label}</div>
              {group.items.map((item) => (
                <button
                  key={item.key}
                  data-label={item.label}
                  onClick={() => {
                    onSelect(item.key)
                    onClose()
                  }}
                  className={`hl-nav-item ${activeTab === item.key ? 'hl-nav-item-active' : ''}`}
                  title={item.label}
                >
                  <span className="hl-nav-icon">{item.icon}</span>
                  <span className="hl-nav-label">{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="hl-nav-foot">
          {!mini && (
            <div className="hl-badge hl-badge-accent" style={{ justifyContent: 'center' }}>
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: 'var(--accent-bright)',
                  boxShadow: '0 0 8px var(--accent-bright)',
                }}
              />
              Gemini 3.6 Flash
            </div>
          )}
          <button
            className="hl-nav-collapse"
            onClick={onToggleMini}
            title={mini ? 'Rozwiń pasek' : 'Zwiń do ikon'}
          >
            {mini ? '»' : '«  Zwiń'}
          </button>
        </div>
      </aside>
    </>
  )
}
