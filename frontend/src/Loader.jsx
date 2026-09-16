import { useEffect, useState } from 'react'

/** Mały spinner w stylu HossaLab. */
export function Spinner({ size = 46 }) {
  return (
    <div className="hl-loader-flask" style={{ width: size, height: size }}>
      <div className="hl-loader-ring" />
      <div className="hl-loader-dot" />
    </div>
  )
}

/**
 * Ekran ładowania z listą kroków, które zapalają się po kolei.
 * Kroki są "kosmetyczne" (nie odzwierciedlają realnego postępu backendu),
 * więc ostatni krok zostaje aktywny aż do faktycznego zakończenia operacji —
 * nie udajemy, że skończyliśmy, dopóki dane naprawdę nie wrócą.
 */
export function StepLoader({ steps, title, intervalMs = 900 }) {
  const [current, setCurrent] = useState(0)

  useEffect(() => {
    setCurrent(0)
    const id = setInterval(() => {
      setCurrent((c) => (c < steps.length - 1 ? c + 1 : c))
    }, intervalMs)
    return () => clearInterval(id)
  }, [steps, intervalMs])

  return (
    <div className="hl-panel hl-fade-up" style={{ padding: '26px 28px', display: 'flex', gap: '22px', alignItems: 'flex-start' }}>
      <Spinner />
      <div style={{ flex: 1, minWidth: 0 }}>
        {title && (
          <div style={{ fontWeight: 700, fontSize: '15px', marginBottom: '10px', color: 'var(--text)' }}>
            {title}
          </div>
        )}
        <div>
          {steps.map((s, i) => (
            <div
              key={s}
              className={`hl-step ${i < current ? 'hl-step-done' : i === current ? 'hl-step-active' : ''}`}
            >
              <span className="hl-step-icon">{i < current ? '✓' : i + 1}</span>
              <span>{s}</span>
            </div>
          ))}
        </div>
        <div className="hl-progress" style={{ marginTop: '14px' }}>
          <div
            className="hl-progress-bar"
            style={{ width: `${((current + 1) / steps.length) * 92}%` }}
          />
        </div>
      </div>
    </div>
  )
}

/** Szkielet tabeli — pokazywany zamiast pustki podczas pierwszego ładowania. */
export function TableSkeleton({ rows = 5, cols = 6 }) {
  return (
    <div className="hl-fade-in">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} style={{ display: 'flex', gap: '12px', padding: '13px 12px', borderBottom: '1px solid rgba(31,41,55,0.5)' }}>
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="hl-skeleton"
              style={{
                height: '13px',
                flex: c === 0 ? 2.2 : 1,
                animationDelay: `${(r * cols + c) * 0.05}s`,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

/** Inline loader do przycisków i małych sekcji. */
export function InlineLoader({ text = 'Ładowanie…' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '11px', color: 'var(--text-muted)', fontSize: '13px', padding: '10px 0' }}>
      <Spinner size={20} />
      <span>{text}</span>
    </div>
  )
}

export default StepLoader
