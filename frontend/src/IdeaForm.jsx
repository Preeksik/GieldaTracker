import { useState } from 'react'
import TickerSearch from './TickerSearch'

const API = 'http://127.0.0.1:8000'

/*
  "Dodaj mój pomysł" - własne przeczucie co do spółki, zapisywane w Dzienniku porad.
  Cena i data zapisują się w chwili dodania i nie da się ich później zmienić - inaczej
  łatwo byłoby (nawet nieświadomie) poprawiać historię i bilans wyglądałby lepiej, niż jest.
  Po terminie wynik zamraża się sam (backend), a pomysł trafia do bilansu "Ty vs AI".
*/

// Na czym opiera się pomysł - Lusterko inwestora porówna potem, które podstawy wychodzą Ci najlepiej
const BASIS = [
  { key: 'raport', label: '📊 raport finansowy' },
  { key: 'insider', label: '👔 transakcje insiderów' },
  { key: 'news', label: '📰 news / wydarzenie' },
  { key: 'wykres', label: '📈 wykres' },
  { key: 'przeczucie', label: '🎲 przeczucie' },
  { key: 'inne', label: 'inne' },
]

const HORIZONS = [
  { key: '1m', label: '1 miesiąc' },
  { key: '3m', label: '3 miesiące' },
  { key: '6m', label: '6 miesięcy' },
  { key: '1r', label: 'rok' },
]

export default function IdeaForm({ onSaved, onCancel }) {
  const [ticker, setTicker] = useState('')
  const [direction, setDirection] = useState('wzrost')
  const [horizon, setHorizon] = useState('3m')
  const [reason, setReason] = useState('')
  const [amount, setAmount] = useState('')
  const [basis, setBasis] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    if (!ticker.trim()) {
      setError('Wybierz spółkę.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/journal/idea`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: ticker.trim(), direction, horizon, reason, basis: basis || null,
          amount: amount ? Number(String(amount).replace(',', '.')) : null,
        }),
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Nie udało się zapisać pomysłu.')
      onSaved?.(d)
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? 'Backend nie odpowiada - czy jest uruchomiony?' : err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="hl-panel hl-fade-up" style={{ padding: '18px 22px', marginBottom: 18, borderLeft: '3px solid var(--accent)' }}>
      <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>💡 Mój pomysł</h3>
      <div className="hl-adv-hint" style={{ margin: '0 0 14px' }}>
        Zapisz przeczucie, zanim się sprawdzi albo nie. Aplikacja zapamięta dzisiejszą cenę i po terminie sama
        policzy wynik - na tle rynku (WIG20 dla GPW, S&P 500 dla reszty). Ceny i daty nie da się potem zmienić.
      </div>

      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div className="hl-adv-field">
          <span className="hl-adv-label">Spółka</span>
          <TickerSearch value={ticker} onChange={setTicker} placeholder="np. CD Projekt, PKN.WA" style={{ width: 260 }} />
        </div>
        <div className="hl-adv-field">
          <span className="hl-adv-label">Co się stanie?</span>
          <div className="hl-adv-chips">
            <button type="button" className={`hl-adv-chip ${direction === 'wzrost' ? 'hl-adv-chip-on' : ''}`}
              onClick={() => setDirection('wzrost')}>📈 Wzrośnie</button>
            <button type="button" className={`hl-adv-chip ${direction === 'spadek' ? 'hl-adv-chip-on' : ''}`}
              onClick={() => setDirection('spadek')}>📉 Spadnie</button>
          </div>
        </div>
        <div className="hl-adv-field">
          <span className="hl-adv-label">W ciągu</span>
          <div className="hl-adv-chips">
            {HORIZONS.map((h) => (
              <button key={h.key} type="button" className={`hl-adv-chip ${horizon === h.key ? 'hl-adv-chip-on' : ''}`}
                onClick={() => setHorizon(h.key)}>{h.label}</button>
            ))}
          </div>
        </div>
        <div className="hl-adv-field">
          <span className="hl-adv-label">Ile byś włożył? (opcjonalnie)</span>
          <input className="hl-input" value={amount} onChange={(e) => setAmount(e.target.value)}
            placeholder="np. 2000 zł" inputMode="decimal" style={{ width: 140 }} />
        </div>
      </div>

      <div className="hl-adv-field" style={{ marginTop: 14 }}>
        <span className="hl-adv-label">Na czym się opierasz?</span>
        <div className="hl-adv-chips">
          {BASIS.map((b) => (
            <button key={b.key} type="button" className={`hl-adv-chip ${basis === b.key ? 'hl-adv-chip-on' : ''}`}
              onClick={() => setBasis(basis === b.key ? '' : b.key)}>{b.label}</button>
          ))}
        </div>
        <span className="hl-adv-hint" style={{ margin: '4px 0 0' }}>
          Po czasie Lusterko inwestora pokaże, na czym Twoje pomysły sprawdzają się najlepiej.
        </span>
      </div>

      <div className="hl-adv-field" style={{ marginTop: 14 }}>
        <span className="hl-adv-label">Dlaczego tak myślisz?</span>
        <textarea className="hl-input" rows={2} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="np. premiera gry w grudniu, a kurs od pół roku stoi w miejscu"
          style={{ resize: 'vertical' }} />
        <span className="hl-adv-hint" style={{ margin: '4px 0 0' }}>
          Warto zapisać - po czasie najwięcej uczy porównanie tego, co myślałeś, z tym, co się stało.
        </span>
      </div>

      {error && <div className="hl-adv-error" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}

      <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
        <button className="hl-btn hl-btn-primary" onClick={save} disabled={saving}>
          {saving ? 'Zapisuję…' : 'Zapisz pomysł'}
        </button>
        <button className="hl-btn" onClick={onCancel} disabled={saving}>Anuluj</button>
      </div>
    </div>
  )
}
