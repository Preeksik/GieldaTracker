import { useState } from 'react'
import MarkdownView from './MarkdownView'
import { StepLoader } from './Loader'

const API = 'http://127.0.0.1:8000'

const HORIZONS = [
  { key: 'krotki', label: 'Do roku' },
  { key: 'sredni', label: '1–3 lata' },
  { key: 'dlugi', label: '3 lata+' },
]
const ACCOUNTS = [
  { key: 'zwykle', label: 'Zwykłe', hint: 'Zyski objęte 19% podatkiem Belki.' },
  { key: 'ike', label: 'IKE', hint: 'Zyski zwolnione z Belki przy wypłacie po 60. roku życia.' },
  { key: 'ikze', label: 'IKZE', hint: 'Wpłaty odliczasz od podstawy opodatkowania, przy wypłacie 10% ryczałtu.' },
]
const RISKS = [
  { key: 'ostrozny', label: 'Ostrożny', hint: 'Priorytetem ochrona kapitału, godzisz się na niższy zwrot.' },
  { key: 'zrownowazony', label: 'Zrównoważony', hint: 'Akceptujesz wahania w zamian za wyższy zwrot w długim terminie.' },
  { key: 'agresywny', label: 'Agresywny', hint: 'Akceptujesz duże obsunięcia w pogoni za ponadprzeciętnym zwrotem.' },
]

function Chips({ options, value, onChange }) {
  return (
    <div className="hl-adv-chips">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          title={o.hint}
          onClick={() => onChange(o.key)}
          className={`hl-adv-chip ${value === o.key ? 'hl-adv-chip-on' : ''}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export default function Advisor() {
  const [amount, setAmount] = useState('10000')
  const [horizon, setHorizon] = useState('dlugi')
  const [account, setAccount] = useState('zwykle')
  const [risk, setRisk] = useState('zrownowazony')
  const [exclusions, setExclusions] = useState('')
  const [question, setQuestion] = useState('')
  const [usePortfolio, setUsePortfolio] = useState(true)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  // Pytania uzupełniające - model dostaje poprzednią część rozmowy jako kontekst.
  const [followUps, setFollowUps] = useState([])
  const [followUpQ, setFollowUpQ] = useState('')
  const [followUpLoading, setFollowUpLoading] = useState(false)

  const call = async (body) => {
    const r = await fetch(`${API}/api/advisor/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      let detail = `Backend zwrócił ${r.status}`
      try {
        const b = await r.json()
        if (b?.detail) detail = b.detail
      } catch { /* odpowiedź bez JSON-a */ }
      throw new Error(detail)
    }
    return r.json()
  }

  const base = () => ({
    amount: parseFloat(amount) || null,
    currency: 'PLN',
    horizon,
    account,
    risk,
    exclusions,
    use_portfolio: usePortfolio,
  })

  const ask = async () => {
    setLoading(true)
    setError('')
    setResult(null)
    setFollowUps([])
    try {
      setResult(await call({ ...base(), question }))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const askFollowUp = async () => {
    if (!followUpQ.trim() || !result) return
    const q = followUpQ.trim()
    setFollowUpLoading(true)
    setFollowUpQ('')
    try {
      // Skracamy historię - przy długiej rozmowie prompt urósłby ponad limit modelu.
      const history = [result.answer, ...followUps.map((f) => `Pytanie: ${f.q}\n${f.a}`)]
        .join('\n\n')
        .slice(-6000)
      const data = await call({ ...base(), question: q, previous_analysis: history })
      setFollowUps((prev) => [...prev, { q, a: data.answer, verified: data.verified }])
    } catch (e) {
      setFollowUps((prev) => [...prev, { q, a: `**Nie udało się odpowiedzieć:** ${e.message}`, verified: [] }])
    } finally {
      setFollowUpLoading(false)
    }
  }

  const Verified = ({ rows }) => {
    if (!rows || rows.length === 0) return null
    const bad = rows.filter((r) => !r.ok)
    return (
      <div className="hl-adv-verify">
        <div className="hl-adv-verify-head">
          Sprawdziłem w Yahoo Finance każdy symbol wymieniony powyżej
          {bad.length > 0 && <span className="hl-adv-bad-count"> · {bad.length} bez potwierdzenia</span>}
        </div>
        <div className="hl-adv-verify-rows">
          {rows.map((r) => (
            <div key={r.ticker} className={`hl-adv-vrow ${r.ok ? '' : 'hl-adv-vrow-bad'}`}>
              <span className="hl-adv-vtick">{r.ticker}</span>
              <span className="hl-adv-vname">{r.ok ? (r.name || '—') : 'brak danych w Yahoo Finance'}</span>
              <span className="hl-adv-vprice">
                {r.ok ? `${r.price} ${r.currency}` : '✗'}
              </span>
            </div>
          ))}
        </div>
        {bad.length > 0 && (
          <div className="hl-adv-warn">
            Symbole oznaczone <strong>✗</strong> nie zwróciły ceny. Albo model podał zły sufiks
            giełdy, albo wymyślił symbol — zanim cokolwiek kupisz, sprawdź je ręcznie.
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div className="hl-panel" style={{ padding: '22px 24px', marginBottom: '20px' }}>
        <h2 style={{ margin: '0 0 6px', fontSize: '17px' }}>Mam pieniądze — co z nimi zrobić?</h2>
        <p style={{ margin: '0 0 20px', color: 'var(--text-muted)', fontSize: '13.5px', lineHeight: 1.55 }}>
          Pytanie od którego wszyscy zaczynają. Działa też, gdy portfel jest jeszcze pusty.
          Każdą spółkę wymienioną w odpowiedzi sprawdzam potem w Yahoo Finance i dopisuję
          realną, dzisiejszą cenę.
        </p>

        <div className="hl-adv-grid">
          <label className="hl-adv-field">
            <span className="hl-adv-label">Kwota (PLN)</span>
            <input
              className="hl-input hl-num"
              type="number"
              min="0"
              step="500"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="10000"
            />
          </label>

          <div className="hl-adv-field">
            <span className="hl-adv-label">Horyzont</span>
            <Chips options={HORIZONS} value={horizon} onChange={setHorizon} />
          </div>

          <div className="hl-adv-field">
            <span className="hl-adv-label">Konto</span>
            <Chips options={ACCOUNTS} value={account} onChange={setAccount} />
          </div>

          <div className="hl-adv-field">
            <span className="hl-adv-label">Apetyt na ryzyko</span>
            <Chips options={RISKS} value={risk} onChange={setRisk} />
          </div>
        </div>

        <div className="hl-adv-hint">
          {ACCOUNTS.find((a) => a.key === account)?.hint}{' '}
          {RISKS.find((r) => r.key === risk)?.hint}
        </div>

        <label className="hl-adv-field" style={{ marginTop: '14px' }}>
          <span className="hl-adv-label">Czego nie chcesz (opcjonalnie)</span>
          <input
            className="hl-input"
            value={exclusions}
            onChange={(e) => setExclusions(e.target.value)}
            placeholder="np. bez kryptowalut, bez spółek węglowych, nic poniżej 1 mld kapitalizacji"
          />
        </label>

        <label className="hl-adv-field" style={{ marginTop: '12px' }}>
          <span className="hl-adv-label">Twoje pytanie (opcjonalnie)</span>
          <textarea
            className="hl-input"
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder='np. "Mam 10k i myślę długoterminowo. Wolę ETF-y czy pojedyncze spółki z GPW?"'
            style={{ resize: 'vertical', lineHeight: 1.5 }}
          />
        </label>

        <div className="hl-adv-actions">
          <label className="hl-adv-check">
            <input
              type="checkbox"
              checked={usePortfolio}
              onChange={(e) => setUsePortfolio(e.target.checked)}
            />
            Uwzględnij mój obecny portfel
          </label>
          <button onClick={ask} disabled={loading} className="hl-btn hl-btn-primary">
            {loading ? 'Myślę…' : 'Zapytaj ▸'}
          </button>
        </div>
      </div>

      {error && (
        <div className="hl-adv-error hl-fade-up">{error}</div>
      )}

      {loading && (
        <StepLoader
          title="Układam plan"
          steps={[
            'Zbieram stan rynków i kursy walut',
            'Czytam Twój portfel i watchlistę',
            'Analityk układa alokację',
            'Sprawdzam każdą wymienioną spółkę w Yahoo Finance',
          ]}
        />
      )}

      {result && (
        <div className="hl-panel hl-panel-glow hl-fade-up" style={{ padding: '24px 26px' }}>
          <MarkdownView>{result.answer}</MarkdownView>
          <Verified rows={result.verified} />

          <div className="hl-adv-follow">
            <div className="hl-adv-label" style={{ marginBottom: '8px' }}>Dopytaj</div>
            {followUps.map((f, i) => (
              <div key={i} className="hl-adv-followitem">
                <div className="hl-adv-followq">{f.q}</div>
                <MarkdownView>{f.a}</MarkdownView>
                <Verified rows={f.verified} />
              </div>
            ))}
            {followUpLoading && <div className="hl-adv-hint">Odpowiadam…</div>}
            <div style={{ display: 'flex', gap: '9px', marginTop: '10px', flexWrap: 'wrap' }}>
              <input
                className="hl-input"
                style={{ flex: 1, minWidth: '240px' }}
                value={followUpQ}
                onChange={(e) => setFollowUpQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !followUpLoading && askFollowUp()}
                placeholder='np. "a gdybym miał 20k zamiast 10k?"'
              />
              <button
                onClick={askFollowUp}
                disabled={followUpLoading || !followUpQ.trim()}
                className="hl-btn"
              >
                Wyślij
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
