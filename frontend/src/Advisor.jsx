import { useState, useEffect } from 'react'
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
  const [brokers, setBrokers] = useState(null)   // u kogo są konta - pokazujemy, dla kogo liczone koszty

  useEffect(() => {
    fetch(`${API}/api/broker/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setBrokers(d.accounts))
      .catch(() => {})
  }, [])

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
      const data = await call({ ...base(), question })
      setResult(data)
      // Jeśli backend odczytał horyzont/ryzyko z treści pytania, ustawiamy chipy tak samo -
      // formularz ma pokazywać to, na podstawie czego faktycznie policzono odpowiedź.
      const it = data.interpreted
      if (it?.from_text?.horizon) setHorizon(it.horizon)
      if (it?.from_text?.risk) setRisk(it.risk)
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
            <div key={r.ticker}>
              <div className={`hl-adv-vrow ${!r.ok || r.fit === false ? 'hl-adv-vrow-bad' : ''}`}>
                <span className="hl-adv-vtick">{r.ticker}</span>
                <span className="hl-adv-vname">
                  {r.ok ? (r.name || '—') : 'brak danych w Yahoo Finance'}
                  {r.ok && r.metrics?.vol_90 != null && (
                    <span className="hl-adv-vmeta"> · zmienność {Math.round(r.metrics.vol_90)}%/rok</span>
                  )}
                  {r.ok && r.from_scanner === false && <span className="hl-adv-vmeta"> · spoza skanera</span>}
                </span>
                <span className="hl-adv-vprice">
                  {r.ok ? `${r.price} ${r.currency}` : '✗'}
                </span>
              </div>
              {r.fit === false && <div className="hl-adv-misfit">⚠ Nie pasuje do profilu: {r.fit_reason}</div>}
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
          {brokers?.[account] && (
            <> Koszty transakcji liczone dla: <strong style={{ color: 'var(--accent)' }}>{brokers[account].name}</strong>
              {' '}(zmienisz w zakładce Broker i koszty).</>
          )}
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
            'Odczytuję profil z Twojego pytania',
            'Skanuję ~70 spółek i ETF-ów pod ten profil',
            'Sprawdzam katalizatory czołówki',
            'Analityk układa alokację',
            'Weryfikuję każdą propozycję w Yahoo Finance',
          ]}
        />
      )}

      {result && (
        <div className="hl-panel hl-panel-glow hl-fade-up" style={{ padding: '24px 26px' }}>
          {result.interpreted && (
            <div className="hl-adv-interp">
              <span className="hl-adv-interp-label">Profil</span>
              <strong>{result.interpreted.profile}</strong>
              {(result.interpreted.from_text?.horizon || result.interpreted.from_text?.risk) && (
                <span> · odczytane z Twojego pytania
                  {result.interpreted.from_text.horizon && <> (horyzont: {HORIZONS.find((h) => h.key === result.interpreted.horizon)?.label})</>}
                  {result.interpreted.from_text.risk && <> (ryzyko: {RISKS.find((r) => r.key === result.interpreted.risk)?.label})</>}
                </span>
              )}
              {result.interpreted.exclusions && <span> · bez: {result.interpreted.exclusions}</span>}
            </div>
          )}

          <MarkdownView>{result.answer}</MarkdownView>
          <Verified rows={result.verified} />

          {result.scan?.candidates?.length > 0 && (
            <details className="hl-adv-scan">
              <summary>
                Skaner wybrał {result.scan.candidates.length} kandydatów z {result.scan.scanned} przeskanowanych
                {result.scan.relaxed && ' (kryteria poluzowane — mało spółek spełniało je w pełni)'}
              </summary>
              <div style={{ overflowX: 'auto' }}>
                <table className="hl-table" style={{ marginTop: 10 }}>
                  <thead>
                    <tr>
                      <th>Spółka</th>
                      <th style={{ textAlign: 'right' }}>Zmienność</th>
                      <th style={{ textAlign: 'right' }}>1M</th>
                      <th style={{ textAlign: 'right' }}>3M</th>
                      <th>Najbliższy raport</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.scan.candidates.map((c) => (
                      <tr key={c.ticker}>
                        <td>
                          <span className="hl-adv-vtick" style={{ minWidth: 0, marginRight: 8 }}>{c.ticker}</span>
                          {c.name} <span className="hl-adv-vmeta">· {c.sector}</span>
                          {c.owned && <span className="hl-ts-owned" style={{ marginLeft: 6 }}>masz</span>}
                        </td>
                        <td className="hl-num" style={{ textAlign: 'right' }}>{Math.round(c.metrics.vol_90)}%</td>
                        <td className={`hl-num ${c.metrics.ret_1m >= 0 ? 'hl-up' : 'hl-down'}`} style={{ textAlign: 'right' }}>
                          {c.metrics.ret_1m != null ? `${c.metrics.ret_1m > 0 ? '+' : ''}${c.metrics.ret_1m}%` : '—'}
                        </td>
                        <td className={`hl-num ${c.metrics.ret_3m >= 0 ? 'hl-up' : 'hl-down'}`} style={{ textAlign: 'right' }}>
                          {c.metrics.ret_3m != null ? `${c.metrics.ret_3m > 0 ? '+' : ''}${c.metrics.ret_3m}%` : '—'}
                        </td>
                        <td className="hl-num">{c.catalysts?.earnings ? String(c.catalysts.earnings).slice(0, 10) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {result.scan.rejected_owned?.length > 0 && (
                <div className="hl-adv-hint">
                  Z Twojego portfela odrzucone jako niepasujące do profilu:{' '}
                  {result.scan.rejected_owned.map(([t, why]) => `${t} (${why})`).join(', ')}.
                </div>
              )}
            </details>
          )}

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