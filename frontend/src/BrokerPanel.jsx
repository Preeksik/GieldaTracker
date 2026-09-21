import { useState, useEffect, useCallback } from 'react'

const API = 'http://127.0.0.1:8000'

const ACCOUNTS = [
  { key: 'zwykle', label: 'Konto zwykłe' },
  { key: 'ike', label: 'IKE' },
  { key: 'ikze', label: 'IKZE' },
]

const zl = (v) =>
  v == null ? '—' : `${Number(v).toLocaleString('pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} zł`

async function api(path, body) {
  const r = await fetch(`${API}${path}`, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : undefined)
  if (!r.ok) {
    let detail = `Backend zwrócił ${r.status}`
    try { const b = await r.json(); if (b?.detail) detail = b.detail } catch { /* brak JSON */ }
    throw new Error(detail)
  }
  return r.json()
}

export default function BrokerPanel() {
  const [presets, setPresets] = useState(null)
  const [profile, setProfile] = useState(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  // kalkulator
  const [amount, setAmount] = useState('10000')
  const [tranches, setTranches] = useState('10')
  const [market, setMarket] = useState('zagranica')
  const [cmp, setCmp] = useState(null)

  const [showPrompt, setShowPrompt] = useState(false)
  const [promptBlock, setPromptBlock] = useState('')
  const [custom, setCustom] = useState(null)

  const load = useCallback(async () => {
    try {
      const [p, pr] = await Promise.all([api('/api/broker/presets'), api('/api/broker/profile')])
      setPresets(p)
      setProfile(pr)
      setCustom(pr.custom)
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const recalc = useCallback(async () => {
    const a = parseFloat(amount)
    const t = parseInt(tranches, 10)
    if (!a || a <= 0 || !t || t < 1) return
    try {
      setCmp(await api('/api/broker/compare', { amount: a, tranches: t, market }))
    } catch (e) {
      setError(e.message)
    }
  }, [amount, tranches, market])

  // Przeliczamy automatycznie, z krótkim opóźnieniem, żeby nie strzelać przy każdej cyfrze.
  useEffect(() => {
    const t = setTimeout(recalc, 250)
    return () => clearTimeout(t)
  }, [recalc, profile])

  const setAccount = async (acc, broker) => {
    try {
      setProfile(await api('/api/broker/profile', { accounts: { [acc]: broker } }))
      flashSaved()
    } catch (e) {
      setError(e.message)
    }
  }

  const saveCustom = async () => {
    try {
      const body = {
        name: custom.name,
        fx_pct: custom.fx_pct === '' || custom.fx_pct == null ? null : parseFloat(custom.fx_pct),
        gpw: { pct: parseFloat(custom.gpw.pct) || 0, min: parseFloat(custom.gpw.min) || 0, min_currency: 'PLN' },
        zagranica: { pct: parseFloat(custom.zagranica.pct) || 0, min: parseFloat(custom.zagranica.min) || 0, min_currency: 'PLN' },
      }
      setProfile(await api('/api/broker/profile', { custom: body }))
      await load()
      flashSaved()
    } catch (e) {
      setError(e.message)
    }
  }

  const flashSaved = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 1800)
  }

  const togglePrompt = async () => {
    if (!showPrompt) {
      try { setPromptBlock((await api('/api/broker/prompt-preview')).block) } catch (e) { setError(e.message) }
    }
    setShowPrompt((v) => !v)
  }

  if (error && !presets) {
    return <div className="hl-adv-error">Nie mogę wczytać danych o brokerach: {error}</div>
  }
  if (!presets || !profile) {
    return <div className="hl-adv-hint">Wczytuję tabele opłat…</div>
  }

  const all = presets.presets
  const chosen = [...new Set(Object.values(profile.accounts).map((a) => a.broker))]
  const usesCustom = chosen.includes('wlasny')
  const key = parseInt(tranches, 10) > 1 ? 'dca' : 'lump'

  return (
    <div>
      {error && <div className="hl-adv-error">{error}</div>}

      {/* ---------- Gdzie masz konta ---------- */}
      <div className="hl-panel" style={{ padding: '22px 24px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0, fontSize: '17px' }}>Gdzie masz konta?</h2>
          {saved && <span className="hl-br-saved">✓ zapisane</span>}
        </div>
        <p className="hl-adv-hint" style={{ margin: '6px 0 18px' }}>
          Każda analiza AI dostaje Twoje realne koszty. Bez tego model zakładał „typowe”
          prowizje i doradzał np. jednorazowy zakup, „bo transze zjedzą zysk” — co w XTB
          po prostu nie jest prawdą.
        </p>

        <div className="hl-br-accounts">
          {ACCOUNTS.map((a) => (
            <label key={a.key} className="hl-adv-field">
              <span className="hl-adv-label">{a.label}</span>
              <select
                className="hl-input"
                value={profile.accounts[a.key]?.broker || 'xtb'}
                onChange={(e) => setAccount(a.key, e.target.value)}
              >
                {Object.entries(all).map(([id, s]) => (
                  <option key={id} value={id}>{s.name}</option>
                ))}
              </select>
            </label>
          ))}
        </div>

        {chosen.map((id) => {
          const s = all[id]
          if (!s) return null
          const accs = ACCOUNTS.filter((a) => profile.accounts[a.key]?.broker === id).map((a) => a.label)
          return (
            <div key={id} className="hl-br-card">
              <div className="hl-br-card-head">
                <strong>{s.name}</strong>
                <span className="hl-br-accs">{accs.join(' · ')}</span>
              </div>
              <ul className="hl-br-notes">
                {(s.notes || []).map((n, i) => <li key={i}>{n}</li>)}
              </ul>
              <div className="hl-br-src">
                {s.source
                  ? <>Źródło: <a href={s.source} target="_blank" rel="noreferrer">{s.source_kind}</a> · sprawdzone {s.checked}</>
                  : <>Stawki wpisane ręcznie</>}
              </div>
            </div>
          )
        })}

        <div className="hl-adv-warn" style={{ marginTop: 14 }}>
          Cenniki maklerskie się zmieniają, a promocje (np. 0% na ETF-y na IKE) bywają czasowe.
          Tabele sprawdziłem {presets.checked} — zanim podejmiesz decyzję na podstawie
          różnicy kilku złotych, zajrzyj do aktualnej tabeli opłat swojego brokera.
        </div>
      </div>

      {/* ---------- Własne stawki ---------- */}
      {usesCustom && custom && (
        <div className="hl-panel" style={{ padding: '20px 24px', marginBottom: '20px' }}>
          <h3 style={{ margin: '0 0 14px', fontSize: '15px' }}>Własne stawki</h3>
          <div className="hl-br-custom">
            <label className="hl-adv-field">
              <span className="hl-adv-label">Nazwa</span>
              <input className="hl-input" value={custom.name || ''}
                onChange={(e) => setCustom({ ...custom, name: e.target.value })} />
            </label>
            {[['gpw', 'GPW'], ['zagranica', 'Zagranica']].map(([m, label]) => (
              <div key={m} className="hl-br-custom-row">
                <label className="hl-adv-field">
                  <span className="hl-adv-label">{label}: prowizja %</span>
                  <input className="hl-input hl-num" type="number" step="0.01" value={custom[m].pct}
                    onChange={(e) => setCustom({ ...custom, [m]: { ...custom[m], pct: e.target.value } })} />
                </label>
                <label className="hl-adv-field">
                  <span className="hl-adv-label">minimum (zł)</span>
                  <input className="hl-input hl-num" type="number" step="0.5" value={custom[m].min}
                    onChange={(e) => setCustom({ ...custom, [m]: { ...custom[m], min: e.target.value } })} />
                </label>
              </div>
            ))}
            <label className="hl-adv-field">
              <span className="hl-adv-label">Przewalutowanie % (puste = nie wiem)</span>
              <input className="hl-input hl-num" type="number" step="0.01" value={custom.fx_pct ?? ''}
                onChange={(e) => setCustom({ ...custom, fx_pct: e.target.value })} />
            </label>
          </div>
          <button className="hl-btn hl-btn-primary" style={{ marginTop: 14 }} onClick={saveCustom}>
            Zapisz stawki
          </button>
        </div>
      )}

      {/* ---------- Kalkulator ---------- */}
      <div className="hl-panel" style={{ padding: '22px 24px', marginBottom: '20px' }}>
        <h2 style={{ margin: '0 0 6px', fontSize: '17px' }}>Jednorazowo czy w transzach?</h2>
        <p className="hl-adv-hint" style={{ margin: '0 0 16px' }}>
          Ile kosztuje wejście tą samą kwotą u każdego brokera. Transze liczone jako zakupy
          w kolejnych miesiącach, więc próg darmowego obrotu w XTB odnawia się przy każdej.
        </p>

        <div className="hl-br-calc">
          <label className="hl-adv-field">
            <span className="hl-adv-label">Kwota (zł)</span>
            <input className="hl-input hl-num" type="number" min="0" step="500"
              value={amount} onChange={(e) => setAmount(e.target.value)} />
          </label>
          <label className="hl-adv-field">
            <span className="hl-adv-label">Liczba transz</span>
            <input className="hl-input hl-num" type="number" min="1" max="60"
              value={tranches} onChange={(e) => setTranches(e.target.value)} />
          </label>
          <div className="hl-adv-field">
            <span className="hl-adv-label">Rynek</span>
            <div className="hl-adv-chips">
              {[['gpw', 'GPW (PLN)'], ['zagranica', 'Zagranica (EUR/USD)']].map(([k, l]) => (
                <button key={k} type="button" onClick={() => setMarket(k)}
                  className={`hl-adv-chip ${market === k ? 'hl-adv-chip-on' : ''}`}>{l}</button>
              ))}
            </div>
          </div>
        </div>

        {cmp && (
          <div style={{ overflowX: 'auto', marginTop: 18 }}>
            <table className="hl-table">
              <thead>
                <tr>
                  <th>Broker</th>
                  <th style={{ textAlign: 'right' }}>Jednorazowo</th>
                  <th style={{ textAlign: 'right' }}>{cmp.tranches > 1 ? `${cmp.tranches} transz` : 'W transzach'}</th>
                  <th style={{ textAlign: 'right' }}>Dopłata za transze</th>
                  <th style={{ textAlign: 'right' }}>% kwoty</th>
                </tr>
              </thead>
              <tbody>
                {cmp.rows.map((r) => (
                  <tr key={r.broker} className={r.mine ? 'hl-br-mine' : ''}>
                    <td>
                      {r.name}
                      {r.mine && <span className="hl-ts-owned" style={{ marginLeft: 8 }}>Twój</span>}
                      {r[key].fx_unknown && <div className="hl-br-incomplete">bez kosztu przewalutowania — nieznany</div>}
                    </td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{zl(r.lump.total)}</td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{zl(r.dca.total)}</td>
                    <td className={`hl-num ${r.extra_cost_of_dca > 0.005 ? 'hl-down' : 'hl-up'}`} style={{ textAlign: 'right' }}>
                      {r.extra_cost_of_dca > 0.005 ? `+${zl(r.extra_cost_of_dca)}` : 'bez dopłaty'}
                    </td>
                    <td className="hl-num" style={{ textAlign: 'right' }}>{r[key].pct_of_amount.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {cmp.rows.filter((r) => r.mine).map((r) => (
              <div key={r.broker} className="hl-br-verdict">
                {r.extra_cost_of_dca <= 0.005 ? (
                  <>U <strong>{r.name}</strong> podział na transze <strong>nic nie kosztuje</strong>.
                    Decyzja jednorazowo czy stopniowo to kwestia ryzyka wejścia w szczyt i Twojej
                    dyscypliny, a nie prowizji.</>
                ) : (
                  <>U <strong>{r.name}</strong> podział na {cmp.tranches} transz kosztuje
                    <strong> {zl(r.extra_cost_of_dca)} więcej</strong>
                    {r.min_commission_trap_below && (
                      <> — przy zakupach poniżej ok. <strong>{zl(r.min_commission_trap_below)}</strong> płacisz
                        minimalną prowizję zamiast procentowej</>
                    )}.</>
                )}
                {market === 'zagranica' && r[key].fx > 0 && (
                  <> Przewalutowanie ({zl(r[key].fx)}) płacisz niezależnie od podziału — i drugi raz przy sprzedaży.</>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ---------- Podgląd promptu ---------- */}
      <button className="hl-btn hl-btn-sm" onClick={togglePrompt}>
        {showPrompt ? 'Ukryj' : 'Pokaż'}, co model wie o Twoich kosztach
      </button>
      {showPrompt && <pre className="hl-br-prompt">{promptBlock}</pre>}
    </div>
  )
}
