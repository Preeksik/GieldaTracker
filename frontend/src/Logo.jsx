import { useEffect, useState } from 'react'

/**
 * Logo HossaLab — kolba laboratoryjna z wybijającym się wykresem hossy.
 * `animated` uruchamia rysowanie linii trendu i unoszące się pęcherzyki.
 */
export function LogoMark({ size = 44, animated = true }) {
  return (
    <div
      style={{ width: size, height: size, flexShrink: 0 }}
      className={animated ? 'hl-logo-animated' : undefined}
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 520" width="100%" height="100%">
        <defs>
          {/* Tło w stylu deep tech/dark mode */}
          <linearGradient id="bgGrad-mark" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#080D1A" />
            <stop offset="100%" stopColor="#030712" />
          </linearGradient>

          {/* Gradient Hossy (Neon Green -> Mint -> Cyan) */}
          <linearGradient id="hossaGrad-mark" x1="0%" y1="100%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#059669" />
            <stop offset="45%" stopColor="#10B981" />
            <stop offset="100%" stopColor="#06B6D4" />
          </linearGradient>

          {/* Gradient cieczy w kolbie laboratoryjnej */}
          <linearGradient id="liquidGrad-mark" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#10B981" stopOpacity="0.85" />
            <stop offset="100%" stopColor="#047857" stopOpacity="0.95" />
          </linearGradient>

          {/* Poświata neonowa (Glow) */}
          <filter id="neonGlow-mark" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="7" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          <filter id="softGlow-mark" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="14" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Ramka i ciemne tło aplikacji */}
        <rect width="520" height="520" rx="100" fill="url(#bgGrad-mark)" stroke="#1F2937" strokeWidth="2.5"/>

        {/* Tło analityczne: subtelna siatka wykresu laboratoryjnego */}
        <g opacity="0.07" stroke="#94A3B8" strokeWidth="1.5">
          <line x1="90" y1="130" x2="430" y2="130" strokeDasharray="4 4"/>
          <line x1="90" y1="190" x2="430" y2="190" strokeDasharray="4 4"/>
          <line x1="90" y1="250" x2="430" y2="250" strokeDasharray="4 4"/>
          <line x1="90" y1="310" x2="430" y2="310" strokeDasharray="4 4"/>
          <line x1="160" y1="90" x2="160" y2="340" />
          <line x1="260" y1="90" x2="260" y2="340" />
          <line x1="360" y1="90" x2="360" y2="340" />
        </g>

        {/* ================= IKONA GŁÓWNA: KOLBA + HOSSA ================= */}
        <g transform="translate(0, -10)">
          {/* Ciecz wewnątrz kolby (reakcja giełdowa) */}
          <path d="M 200 245 L 165 305 C 158 317 167 332 181 332 L 339 332 C 353 332 362 317 355 305 L 320 245 Z" 
                fill="url(#liquidGrad-mark)" opacity="0.28" />

          {/* Pęcherzyki / cząstki kwantowe w probówce */}
          <circle cx="230" cy="290" r="4.5" fill="#34D399" opacity="0.6"/>
          <circle cx="285" cy="275" r="3.5" fill="#34D399" opacity="0.8"/>
          <circle cx="255" cy="310" r="5" fill="#06B6D4" opacity="0.5"/>
          <circle cx="295" cy="305" r="3" fill="#10B981" opacity="0.7"/>

          {/* Szklany obrys kolby laboratoryjnej */}
          {/* Szyjka, podziałka laboratoryjna i podstawa */}
          <path d="M 235 155 L 285 155 M 240 155 L 240 200 L 160 325 C 150 341 162 355 180 355 L 340 355 C 358 355 370 341 360 325 L 280 200 L 280 155" 
                fill="none" stroke="#E2E8F0" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" opacity="0.9"/>
    
          {/* Miarka / Skala laboratoryjna na ściance kolby */}
          <line x1="238" y1="245" x2="252" y2="245" stroke="#94A3B8" strokeWidth="3.5" strokeLinecap="round"/>
          <line x1="223" y1="270" x2="243" y2="270" stroke="#94A3B8" strokeWidth="3.5" strokeLinecap="round"/>
          <line x1="205" y1="295" x2="230" y2="295" stroke="#94A3B8" strokeWidth="3.5" strokeLinecap="round"/>

          {/* Świece giełdowe wznoszące się wewnątrz i wybijające w górę */}
          {/* Świeca 1 (niska) */}
          <line x1="215" y1="270" x2="215" y2="320" stroke="#10B981" strokeWidth="2.5"/>
          <rect x="209" y="280" width="12" height="28" rx="2.5" fill="#10B981"/>

          {/* Świeca 2 (przełamująca) */}
          <line x1="260" y1="205" x2="260" y2="280" stroke="#10B981" strokeWidth="3"/>
          <rect x="253" y="220" width="14" height="42" rx="3" fill="#10B981" filter="url(#neonGlow-mark)"/>

          {/* Dynamiczny wykres Hossy wybity wprost z kolby (Breakout) */}
          <path d="M 215 295 L 260 235 L 295 255 L 365 140 L 405 150 M 365 140 L 360 180" 
                fill="none" stroke="url(#hossaGrad-mark)" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" filter="url(#neonGlow-mark)"/>

          {/* Główna świeca breakoutowa na szczycie trendu */}
          <line x1="365" y1="105" x2="365" y2="195" stroke="#00F5A0" strokeWidth="3"/>
          <rect x="357" y="125" width="16" height="50" rx="3" fill="#00F5A0" filter="url(#softGlow-mark)"/>

          {/* Węzły analityczne AI na załamaniach linii */}
          <circle cx="260" cy="235" r="5" fill="#FFFFFF"/>
          <circle cx="295" cy="255" r="5" fill="#FFFFFF"/>
          <circle cx="365" cy="140" r="6.5" fill="#00F5A0" filter="url(#neonGlow-mark)"/>
        </g>

        </svg>
    </div>
  )
}

export function Wordmark({ size = 26 }) {
  return (
    <span
      style={{
        fontSize: size,
        fontWeight: 900,
        letterSpacing: '1.5px',
        lineHeight: 1,
        color: 'var(--text)',
      }}
    >
      HOSSA
      <span
        style={{
          background: 'var(--gradient-hossa)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          backgroundClip: 'text',
        }}
      >
        LAB
      </span>
    </span>
  )
}

/** Pełny lockup do headera: logo + nazwa + tagline. */
export function BrandLockup() {
  const [mounted, setMounted] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 60)
    return () => clearTimeout(t)
  }, [])

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '14px',
        opacity: mounted ? 1 : 0,
        transform: mounted ? 'none' : 'translateY(-10px)',
        transition: 'opacity 0.6s var(--ease), transform 0.6s var(--ease)',
      }}
    >
      <LogoMark size={46} />
      <div>
        <Wordmark size={25} />
        <div
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: '9.5px',
            fontWeight: 600,
            letterSpacing: '4.2px',
            color: 'var(--text-dim)',
            marginTop: '3px',
          }}
        >
          AI QUANT RESEARCH
        </div>
      </div>
    </div>
  )
}

export default LogoMark
