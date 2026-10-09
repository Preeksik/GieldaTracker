import { useState, useEffect } from 'react'
import MarkdownView from './MarkdownView'
import { StepLoader } from './Loader'

// Przegląd portfela: "co jest nie tak z tym, co już mam".
// Pytanie "co kupić za nowe pieniądze" obsługuje Doradca - tu nie ma czatu.
// Wszystkie liczby liczy backend (review.py); AI tylko je komentuje.

const API_URL = 'http://127.0.0.1:8000'

const zl = (v) => `${Math.round(v || 0).toLocaleString('pl-PL')} zł`
const pct = (v, d = 1) => `${(v ?? 0).toFixed(d).replace('.', ',')}%`

const LEVELS = {
  serious: { icon: '‼', label: 'Ryzyko', cls: 'hl-rv-serious' },
  warning: { icon: '⚠', label: 'Uwaga', cls: 'hl-rv-warning' },
  info: { icon: 'ℹ', label: 'Do wiadomości', cls: 'hl-rv-info' },
  good: { icon: '✓', label: 'Okazja', cls: 'hl-rv-good' },
}

// Udział w całości: jeden kolor (wielkość), wartość opisana przy każdym słupku.
function BarList({ title, items, note, max = 8 }) {
  if (!items?.length) return null
  let rows = items
  if (items.length > max) {
    const rest = items.slice(max - 1)
    rows = [
      ...items.slice(0, max - 1),
      { label: `Pozostałe (${rest.length})`, value: rest.reduce((s, x) => s + x.value, 0), pct: rest.reduce((s, x) => s + x.pct, 0) },
    ]
  }
  const top = Math.max(...rows.map((r) => r.pct), 1)
  return (
    <div className="hl-panel hl-rv-card">
      <h3>{title}</h3>
      <div className="hl-rv-bars" role="list">
        {rows.map((r) => (
          <div key={r.label} className="hl-rv-bar" role="listitem" title={`${r.label}: ${pct(r.pct)} · ${zl(r.value)}`}>
            <span className="hl-rv-bar-label">{r.label}</span>
            <span className="hl-rv-bar-track">
              <span className="hl-rv-bar-fill" style={{ width: `${(r.pct / top) * 100}%` }} />
            </span>
            <span className="hl-rv-bar-value">
              <b>{pct(r.pct)}</b> <span>{zl(r.value)}</span>
            </span>
          </div>
        ))}
      </div>
      {note && <div className="hl-rv-note">{note}</div>}
    </div>
  )
}

function PortfolioReport() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [ai, setAi] = useState(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/review`)
      if (!res.ok) {
        const e = await res.json().catch(() => null)
        throw new Error(e?.detail || 'Nie udało się przygotować przeglądu portfela.')
      }
      setData(await res.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const askAi = async () => {
    setAiLoading(true)
    setAiError('')
    try {
      const res = await fetch(`${API_URL}/api/review/ai`, { method: 'POST' })
      if (!res.ok) {
        const e = await res.json().catch(() => null)
        throw new Error(e?.detail || 'Model nie odpowiedział.')
      }
      const d = await res.json()
      setAi(d)
      if (d.review) setData(d.review)
    } catch (e) {
      setAiError(e.message)
    } finally {
      setAiLoading(false)
    }
  }

  if (loading && !data) {
    return (
      <StepLoader
        title="Przeglądam portfel"
        steps={['Wyceniam pozycje', 'Ustalam kraje, sektory i rodzaj instrumentów', 'Liczę udziały, waluty i koszty', 'Sprawdzam podatek na zwykłym koncie']}
      />
    )
  }
  if (error) return <div className="hl-adv-error">{error}</div>
  if (!data) return null

  const t = data.tax
  const c = data.costs
  const positions = data.positions.map((p) => ({ label: p.name, value: p.value, pct: p.weight }))

  return (
    <div>
      <div className="hl-panel" style={{ padding: '20px 24px', marginBottom: 16 }}>
        <div className="hl-rd-head">
          <div>
            <h2 style={{ margin: 0, fontSize: 17 }}>Przegląd portfela</h2>
            <div className="hl-adv-hint" style={{ margin: '4px 0 0' }}>
              Stan tego, co już masz. Pytanie „co kupić za nowe pieniądze” zadaj Doradcy.
            </div>
          </div>
          <button className="hl-btn" onClick={load} disabled={loading}>
            {loading ? 'Liczę…' : 'Odśwież'}
          </button>
        </div>

        <div className="hl-jr-summary" style={{ marginTop: 16 }}>
          <div><b>{zl(data.total_value)}</b><span>wartość</span></div>
          <div title="Ile równych pozycji odpowiada Twoim wagom. 10 instrumentów, z których jeden to 60%, zachowuje się jak ok. 2-3.">
            <b>{String(data.effective_n).replace('.', ',')}</b><span>efektywnie pozycji (z {data.positions_count})</span>
          </div>
          <div><b>{pct(data.top_weight)}</b><span>największa pozycja</span></div>
          <div><b>{pct(data.foreign_pct, 0)}</b><span>w walutach obcych</span></div>
        </div>
      </div>

      {data.warnings.length > 0 && (
        <div className="hl-panel" style={{ padding: '16px 20px', marginBottom: 16 }}>
          <h3 className="hl-rv-h">Na co zwrócić uwagę</h3>
          <div className="hl-rv-warnings">
            {data.warnings.map((w) => {
              const lv = LEVELS[w.level] || LEVELS.info
              return (
                <div key={w.title} className={`hl-rv-warn ${lv.cls}`}>
                  <span className="hl-rv-warn-icon" aria-hidden="true">{lv.icon}</span>
                  <div>
                    <div className="hl-rv-warn-title">
                      <span className="hl-rv-warn-tag">{lv.label}</span> {w.title}
                    </div>
                    <div className="hl-rv-warn-text">{w.text}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="hl-rv-grid">
        <BarList title="Największe pozycje" items={positions} note={`Trzy największe to ${pct(data.top3_weight)} portfela.`} />
        <BarList
          title="Region"
          items={data.regions}
          note="ETF-y przypisane według indeksu: FTSE All-World to „Świat”, NASDAQ 100 i S&P 500 to „USA”."
        />
        <BarList
          title="Waluta notowania"
          items={data.currencies}
          note="Waluta, w której XTB przelicza zakup i sprzedaż. ETF na świat notowany w EUR i tak w większości trzyma spółki z USA."
        />
        <BarList title="Rodzaj" items={data.types} />
        <BarList title="Konto" items={data.accounts} />
        <BarList title="Sektory (tylko akcje)" items={data.sectors} note="ETF-y pominięte — to koszyki wielu sektorów." />
      </div>

      {data.fx_sensitivity.length > 0 && (
        <div className="hl-panel hl-rv-card" style={{ marginBottom: 16 }}>
          <h3>Co robi kurs waluty</h3>
          <div style={{ overflowX: 'auto' }}>
            <table className="hl-table">
              <thead>
                <tr>
                  <th>Waluta</th>
                  <th style={{ textAlign: 'right' }}>Wartość</th>
                  <th style={{ textAlign: 'right' }}>Udział</th>
                  <th style={{ textAlign: 'right' }}>Ruch kursu o 10%</th>
                </tr>
              </thead>
              <tbody>
                {data.fx_sensitivity.map((f) => (
                  <tr key={f.currency}>
                    <td>{f.currency}/PLN</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{zl(f.value)}</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{pct(f.pct)}</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>± {zl(f.move_10pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="hl-rv-note">
            Jeśli złoty umocni się o 10% wobec danej waluty, wartość tej części portfela spadnie o tyle — nawet gdy same
            spółki stoją w miejscu.
          </div>
        </div>
      )}

      <div className="hl-rv-grid">
        <div className="hl-panel hl-rv-card">
          <h3>Koszty przewalutowania (12 miesięcy)</h3>
          <div className="hl-rv-big">{c.fx_unknown && !c.fx_cost_12m ? '—' : `ok. ${zl(c.fx_cost_12m)}`}</div>
          <div className="hl-rv-note">
            Od {zl(c.fx_turnover_12m)} zakupów i sprzedaży w obcych walutach. Opłata za wymianę jest wzięta z zakładki
            „Broker i koszty” (XTB: 0,5% przy każdej transakcji w EUR/USD).
            {c.fx_unknown && ' Dla części kont broker nie podaje tej opłaty — wynik jest zaniżony.'}
          </div>
        </div>

        <div className="hl-panel hl-rv-card">
          <h3>Podatek {t.year} — zwykłe konto</h3>
          <div className="hl-rv-kv">
            <span>Zrealizowany zysk</span><b>{zl(t.realized_regular)}</b>
            <span>Belka do zapłaty (19%)</span><b>{zl(t.belka_due)}</b>
            <span>Niezrealizowany zysk</span><b>{zl(t.unrealized_gain_regular)}</b>
            <span>Niezrealizowane straty</span><b>{zl(t.unrealized_loss_regular)}</b>
          </div>
          {t.harvest_saving > 0 && (
            <div className="hl-rv-note">
              Sprzedaż pozycji ze stratą przed końcem roku obniżyłaby podatek o ok. <b>{zl(t.harvest_saving)}</b>:{' '}
              {t.losers.map((l) => `${l.name} (${zl(l.loss)})`).join(', ')}. Sprawdź zasady rozliczenia z księgowym
              albo w objaśnieniach do PIT-38.
            </div>
          )}
          {t.dividends_on_regular.length > 0 && (
            <div className="hl-rv-note">
              Dywidendowe na zwykłym koncie:{' '}
              {t.dividends_on_regular.map((d) => `${d.name} (stopa ${String(d.yield).replace('.', ',')}%)`).join(', ')} —
              na IKE/IKZE dywidenda nie traci 19%.
            </div>
          )}
        </div>
      </div>

      <div className="hl-panel hl-panel-glow" style={{ padding: '20px 24px', marginTop: 16 }}>
        <div className="hl-rd-head">
          <div>
            <h3 className="hl-rv-h" style={{ margin: 0 }}>Komentarz AI</h3>
            <div className="hl-adv-hint" style={{ margin: '4px 0 0' }}>
              Model dostaje powyższe liczby i ocenia portfel. Niczego nie przelicza sam.
            </div>
          </div>
          <button className="hl-btn hl-btn-primary" onClick={askAi} disabled={aiLoading}>
            {aiLoading ? 'Analizuję…' : ai ? 'Oceń ponownie' : 'Oceń portfel ▸'}
          </button>
        </div>
        {aiError && <div className="hl-adv-error" style={{ marginTop: 12 }}>{aiError}</div>}
        {ai && (
          <div className="hl-fade-up" style={{ marginTop: 14 }}>
            <MarkdownView>{ai.commentary}</MarkdownView>
            {ai.journal_id && (
              <div className="hl-adv-hint" style={{ marginTop: 10 }}>
                ✓ Zapisane w Dzienniku porad — za kilka miesięcy zobaczysz, czy zalecenia się sprawdziły.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default PortfolioReport