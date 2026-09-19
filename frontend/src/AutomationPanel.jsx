import { useState, useEffect } from 'react'
import MarkdownView from './MarkdownView'
import { Spinner } from './Loader'

const API_URL = 'http://127.0.0.1:8000'

function AutomationPanel() {
  const [cfg, setCfg] = useState(null)
  const [meta, setMeta] = useState({ telegram_configured: false, jobs: [], timezone: '' })
  const [results, setResults] = useState({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [toast, setToast] = useState(null)
  const [showSetup, setShowSetup] = useState(false)
  const [openResult, setOpenResult] = useState(null)

  const fetchAll = async () => {
    setError('')
    try {
      const [cRes, rRes] = await Promise.all([
        fetch(`${API_URL}/api/automation/config`),
        fetch(`${API_URL}/api/automation/results`),
      ])
      if (!cRes.ok) throw new Error('Nie udało się pobrać konfiguracji automatyzacji.')
      const cData = await cRes.json()
      setCfg(cData.config)
      setMeta({
        telegram_configured: cData.telegram_configured,
        jobs: cData.jobs || [],
        timezone: cData.timezone,
      })
      if (rRes.ok) setResults((await rRes.json()).results || {})
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [])

  const patch = async (changes) => {
    const optimistic = { ...cfg, ...changes }
    setCfg(optimistic)
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/automation/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      })
      if (!res.ok) throw new Error('Nie udało się zapisać ustawień.')
      const data = await res.json()
      setCfg(data.config)
      fetchAll() // odświeżamy czasy następnych uruchomień
    } catch (err) {
      setError(err.message)
      fetchAll() // cofamy optymistyczną zmianę
    } finally {
      setSaving(false)
    }
  }

  const runNow = async (kind) => {
    setBusy(kind)
    setError('')
    setToast(null)
    try {
      const res = await fetch(`${API_URL}/api/automation/run/${kind}`, { method: 'POST' })
      if (!res.ok) throw new Error('Nie udało się uruchomić zadania.')
      const data = await res.json()
      if (data.result?.error) {
        setError(`Zadanie zakończyło się błędem: ${data.result.error}`)
      } else {
        setToast({ type: 'ok', text: kind === 'digest' ? 'Briefing wygenerowany i wysłany.' : 'Skan ESPI zakończony i wysłany.' })
      }
      fetchAll()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const testTelegram = async () => {
    setBusy('telegram')
    setError('')
    setToast(null)
    try {
      const res = await fetch(`${API_URL}/api/telegram/test`, { method: 'POST' })
      const data = await res.json()
      setToast(
        data.sent
          ? { type: 'ok', text: 'Wiadomość testowa wysłana — sprawdź Telegrama.' }
          : { type: 'warn', text: data.info || 'Nie udało się wysłać.' }
      )
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const markRead = async (key) => {
    await fetch(`${API_URL}/api/automation/results/${key}/read`, { method: 'POST' }).catch(() => {})
    setResults((r) => ({ ...r, [key]: { ...r[key], unread: false } }))
  }

  const toggleResult = (key) => {
    const next = openResult === key ? null : key
    setOpenResult(next)
    if (next && results[key]?.unread) markRead(key)
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', color: 'var(--text-muted)' }}>
        <Spinner size={22} /> Wczytuję ustawienia automatyzacji…
      </div>
    )
  }

  const jobTime = (id) => {
    const job = meta.jobs.find((j) => j.id === id)
    if (!job?.next_run) return null
    return new Date(job.next_run).toLocaleString('pl-PL', {
      weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  }

  const Toggle = ({ checked, onChange, label, hint }) => (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', cursor: 'pointer', padding: '7px 0' }}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: '3px' }} />
      <span>
        <span style={{ fontSize: '13.5px' }}>{label}</span>
        {hint && <div style={{ color: 'var(--text-dim)', fontSize: '11.5px', marginTop: '2px' }}>{hint}</div>}
      </span>
    </label>
  )

  const numInput = { width: '62px', textAlign: 'center' }

  return (
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>⚙️ Automatyzacja</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '22px', maxWidth: '720px' }}>
        Briefing i skaner ESPI uruchamiają się same, a wyniki trafiają na Telegrama. Działa tylko
        wtedy, gdy backend jest uruchomiony — to nie jest usługa w chmurze.
      </p>

      {error && (
        <div style={{ color: 'var(--down)', background: 'rgba(255,91,127,0.08)', border: '1px solid rgba(255,91,127,0.3)', padding: '12px 16px', borderRadius: 'var(--radius-sm)', marginBottom: '18px' }}>
          {error}
        </div>
      )}
      {toast && (
        <div className="hl-fade-up" style={{
          color: toast.type === 'ok' ? 'var(--up)' : 'var(--warn)',
          background: toast.type === 'ok' ? 'rgba(0,245,160,0.07)' : 'rgba(251,191,36,0.07)',
          border: `1px solid ${toast.type === 'ok' ? 'rgba(0,245,160,0.28)' : 'rgba(251,191,36,0.28)'}`,
          padding: '12px 16px', borderRadius: 'var(--radius-sm)', marginBottom: '18px',
        }}>
          {toast.type === 'ok' ? '✓ ' : '⚠ '}{toast.text}
        </div>
      )}

      {/* ---------------- Telegram ---------------- */}
      <div className="hl-panel" style={{ padding: '20px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '18px' }}>✈️</span>
            <div>
              <div style={{ fontWeight: 700 }}>Powiadomienia Telegram</div>
              <div style={{ fontSize: '12.5px', color: meta.telegram_configured ? 'var(--up)' : 'var(--warn)' }}>
                {meta.telegram_configured ? '● Podłączony' : '○ Nieskonfigurowany — działa tylko w apce'}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={() => setShowSetup((s) => !s)} className="hl-btn hl-btn-sm">
              {showSetup ? 'Ukryj instrukcję' : 'Jak podłączyć?'}
            </button>
            <button
              onClick={testTelegram}
              disabled={busy === 'telegram' || !meta.telegram_configured}
              className="hl-btn hl-btn-sm hl-btn-primary"
            >
              {busy === 'telegram' ? 'Wysyłam…' : 'Wyślij test'}
            </button>
          </div>
        </div>

        {showSetup && (
          <div className="hl-fade-up" style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border)', fontSize: '13.5px', lineHeight: '1.75' }}>
            <ol style={{ paddingLeft: '20px', margin: 0 }}>
              <li>W Telegramie napisz do <strong>@BotFather</strong>, wyślij <code>/newbot</code> i podaj nazwę. Dostaniesz <strong>token</strong>.</li>
              <li>Napisz cokolwiek do swojego nowego bota (inaczej nie może się odezwać pierwszy).</li>
              <li>Napisz do <strong>@userinfobot</strong> — odeśle Twoje <strong>chat ID</strong>.</li>
              <li>Wklej oba do <code>backend/.env</code>:
                <pre style={{ background: 'var(--bg-void)', padding: '11px 13px', borderRadius: '7px', marginTop: '7px', overflowX: 'auto', fontSize: '12.5px' }}>
{`TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789`}
                </pre>
              </li>
              <li>Zrestartuj backend i kliknij <strong>Wyślij test</strong>.</li>
            </ol>
          </div>
        )}

        {meta.telegram_configured && (
          <div style={{ marginTop: '14px', paddingTop: '14px', borderTop: '1px solid var(--border)' }}>
            <div style={{ fontSize: '11.5px', textTransform: 'uppercase', letterSpacing: '0.6px', color: 'var(--text-dim)', fontWeight: 600, marginBottom: '4px' }}>
              Co wysyłać na telefon
            </div>
            <div style={{ display: 'flex', gap: '26px', flexWrap: 'wrap' }}>
              <Toggle checked={cfg.telegram_digest} onChange={(v) => patch({ telegram_digest: v })} label="Poranny briefing" />
              <Toggle checked={cfg.telegram_espi} onChange={(v) => patch({ telegram_espi: v })} label="Komunikaty ESPI" />
              <Toggle checked={cfg.telegram_alerts} onChange={(v) => patch({ telegram_alerts: v })} label="Alerty cenowe i dywidendy" />
            </div>
          </div>
        )}
      </div>

      {/* ---------------- Harmonogram ---------------- */}
      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', marginBottom: '20px' }}>
        {/* Briefing */}
        <div className="hl-panel" style={{ flex: '1 1 340px', padding: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
            <div style={{ fontWeight: 700 }}>☕ Poranny briefing</div>
            <Toggle checked={cfg.digest_enabled} onChange={(v) => patch({ digest_enabled: v })} label="" />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', opacity: cfg.digest_enabled ? 1 : 0.45 }}>
            <span style={{ fontSize: '13.5px', color: 'var(--text-muted)' }}>Codziennie o</span>
            <input
              type="number" min="0" max="23" value={cfg.digest_hour} disabled={!cfg.digest_enabled}
              onChange={(e) => patch({ digest_hour: parseInt(e.target.value || 0) })}
              className="hl-input hl-num" style={numInput}
            />
            <span style={{ color: 'var(--text-dim)' }}>:</span>
            <input
              type="number" min="0" max="59" step="5" value={cfg.digest_minute} disabled={!cfg.digest_enabled}
              onChange={(e) => patch({ digest_minute: parseInt(e.target.value || 0) })}
              className="hl-input hl-num" style={numInput}
            />
          </div>

          <div style={{ opacity: cfg.digest_enabled ? 1 : 0.45 }}>
            <Toggle
              checked={cfg.digest_weekdays_only}
              onChange={(v) => patch({ digest_weekdays_only: v })}
              label="Tylko dni robocze"
              hint="W weekend giełda nie pracuje — briefing byłby wczorajszy."
            />
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border)', gap: '10px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-dim)' }}>
              {jobTime('hossalab_digest') ? `Następny: ${jobTime('hossalab_digest')}` : 'Wyłączony'}
            </span>
            <button onClick={() => runNow('digest')} disabled={busy === 'digest'} className="hl-btn hl-btn-sm">
              {busy === 'digest' ? 'Generuję…' : '▶ Uruchom teraz'}
            </button>
          </div>
        </div>

        {/* ESPI */}
        <div className="hl-panel" style={{ flex: '1 1 340px', padding: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
            <div style={{ fontWeight: 700 }}>📜 Skaner ESPI</div>
            <Toggle checked={cfg.espi_enabled} onChange={(v) => patch({ espi_enabled: v })} label="" />
          </div>

          <div style={{ opacity: cfg.espi_enabled ? 1 : 0.45 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '13.5px', color: 'var(--text-muted)' }}>Co</span>
              <input
                type="number" min="1" max="24" value={cfg.espi_every_hours} disabled={!cfg.espi_enabled}
                onChange={(e) => patch({ espi_every_hours: parseInt(e.target.value || 1) })}
                className="hl-input hl-num" style={numInput}
              />
              <span style={{ fontSize: '13.5px', color: 'var(--text-muted)' }}>godz., między</span>
              <input
                type="number" min="0" max="23" value={cfg.espi_start_hour} disabled={!cfg.espi_enabled}
                onChange={(e) => patch({ espi_start_hour: parseInt(e.target.value || 0) })}
                className="hl-input hl-num" style={numInput}
              />
              <span style={{ color: 'var(--text-dim)' }}>–</span>
              <input
                type="number" min="0" max="23" value={cfg.espi_end_hour} disabled={!cfg.espi_enabled}
                onChange={(e) => patch({ espi_end_hour: parseInt(e.target.value || 23) })}
                className="hl-input hl-num" style={numInput}
              />
            </div>
            <div style={{ color: 'var(--text-dim)', fontSize: '11.5px' }}>
              Powiadomienie leci tylko wtedy, gdy treść się <strong>zmieniła</strong> — inaczej co {cfg.espi_every_hours}h
              dostawałbyś ten sam komunikat i przestałbyś je czytać.
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border)', gap: '10px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-dim)' }}>
              {jobTime('hossalab_espi') ? `Następny: ${jobTime('hossalab_espi')}` : 'Wyłączony'}
            </span>
            <button onClick={() => runNow('espi')} disabled={busy === 'espi'} className="hl-btn hl-btn-sm">
              {busy === 'espi' ? 'Skanuję…' : '▶ Skanuj teraz'}
            </button>
          </div>
        </div>
      </div>

      {/* ---------------- Ostatnie wyniki ---------------- */}
      <h3 style={{ fontSize: '15px', marginBottom: '12px', color: 'var(--text-muted)' }}>Ostatnie uruchomienia</h3>

      {Object.keys(results).length === 0 ? (
        <div style={{ color: 'var(--text-dim)', fontSize: '13px' }}>
          Nic jeszcze nie zostało uruchomione automatycznie. Kliknij „Uruchom teraz”, żeby sprawdzić od razu.
        </div>
      ) : (
        [
          { key: 'digest', icon: '☕', label: 'Poranny briefing' },
          { key: 'espi', icon: '📜', label: 'Skan ESPI' },
        ].map(({ key, icon, label }) => {
          const r = results[key]
          if (!r) return null
          const open = openResult === key
          return (
            <div key={key} className="hl-panel" style={{ padding: '14px 18px', marginBottom: '10px' }}>
              <div
                onClick={() => toggleResult(key)}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', gap: '12px', flexWrap: 'wrap' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span>{icon}</span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '13.5px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {label}
                      {r.unread && (
                        <span className="hl-badge hl-badge-accent" style={{ fontSize: '10px', padding: '2px 7px' }}>nowe</span>
                      )}
                      {r.changed === false && (
                        <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>bez zmian</span>
                      )}
                    </div>
                    <div style={{ fontSize: '11.5px', color: 'var(--text-dim)' }}>
                      {new Date(r.generated_at).toLocaleString('pl-PL')}
                    </div>
                  </div>
                </div>
                <span style={{ color: 'var(--text-dim)', fontSize: '12px' }}>{open ? '▼ zwiń' : '▶ pokaż'}</span>
              </div>

              {r.error && (
                <div style={{ color: 'var(--down)', fontSize: '12.5px', marginTop: '8px' }}>
                  Błąd: {r.error}
                </div>
              )}

              {open && r.report && (
                <div className="hl-fade-up" style={{ marginTop: '14px', paddingTop: '14px', borderTop: '1px solid var(--border)' }}>
                  <MarkdownView>{r.report}</MarkdownView>
                </div>
              )}
            </div>
          )
        })
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '11px', marginTop: '20px' }}>
        {saving && 'Zapisuję… · '}
        Strefa czasowa: {meta.timezone || 'Europe/Warsaw'}. Zadania działają wyłącznie przy uruchomionym backendzie —
        po wyłączeniu komputera nic się nie wykona, a zaległe uruchomienia nie są nadrabiane.
      </p>
    </div>
  )
}

export default AutomationPanel
