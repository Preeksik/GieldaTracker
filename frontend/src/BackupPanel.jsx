import { useState, useEffect } from 'react'
import { Spinner } from './Loader'

const API_URL = 'http://127.0.0.1:8000'

const LABELS = {
  portfolio: 'Pozycje w portfelu',
  sales: 'Zrealizowane sprzedaże',
  watchlist: 'Watchlista',
  alerts: 'Alerty cenowe',
  history: 'Historia wartości (live)',
}

function BackupPanel() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState(null)
  const [confirmRestore, setConfirmRestore] = useState(null)

  const fetchStatus = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/backup/status`)
      if (!res.ok) throw new Error('Nie udało się pobrać stanu danych.')
      setStatus(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStatus()
  }, [])

  const handleExport = async () => {
    setBusy('export')
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/backup/export`)
      if (!res.ok) throw new Error('Nie udało się wyeksportować danych.')
      const bundle = await res.json()

      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')
      const a = document.createElement('a')
      a.href = url
      a.download = `hossalab-backup-${stamp}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)

      const total = Object.values(bundle.data || {}).reduce(
        (sum, v) => sum + (Array.isArray(v) ? v.length : 1), 0
      )
      setMessage({ type: 'ok', text: `Pobrano kopię zapasową (${total} rekordów).` })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const handleImport = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setBusy('import')
    setError('')
    setMessage(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch(`${API_URL}/api/backup/import`, { method: 'POST', body: formData })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się wczytać kopii zapasowej.')
      }
      const data = await res.json()
      const parts = Object.entries(data.restored).map(([k, v]) => `${LABELS[k] || k}: ${v}`)
      setMessage({
        type: 'ok',
        text: `Przywrócono z kopii z ${data.backup_created_at || 'nieznanej daty'}. ${parts.join(' · ')}`,
        safety: data.safety_snapshot,
      })
      fetchStatus()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
      e.target.value = ''
    }
  }

  const handleSnapshot = async () => {
    setBusy('snapshot')
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/backup/snapshot`, { method: 'POST' })
      if (!res.ok) throw new Error('Nie udało się zapisać migawki.')
      const data = await res.json()
      setMessage({ type: 'ok', text: `Zapisano migawkę na serwerze: ${data.snapshot}` })
      fetchStatus()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const handleRestore = async (name) => {
    setBusy(name)
    setError('')
    setConfirmRestore(null)
    try {
      const res = await fetch(`${API_URL}/api/backup/restore/${encodeURIComponent(name)}`, { method: 'POST' })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się przywrócić migawki.')
      }
      const data = await res.json()
      const parts = Object.entries(data.restored).map(([k, v]) => `${LABELS[k] || k}: ${v}`)
      setMessage({ type: 'ok', text: `Przywrócono z ${data.from}. ${parts.join(' · ')}`, safety: data.safety_snapshot })
      fetchStatus()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const handleDeleteSnapshot = async (name) => {
    try {
      const res = await fetch(`${API_URL}/api/backup/snapshot/${encodeURIComponent(name)}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Nie udało się usunąć migawki.')
      fetchStatus()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>💾 Kopia zapasowa</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '22px', maxWidth: '720px' }}>
        Wszystkie Twoje dane (portfel, sprzedaże, alerty, watchlista) siedzą w plikach JSON na dysku.
        Stąd pobierzesz je jednym kliknięciem albo przywrócisz z wcześniejszej kopii.
      </p>

      {error && (
        <div style={{ color: 'var(--down)', background: 'rgba(255,91,127,0.08)', border: '1px solid rgba(255,91,127,0.3)', padding: '12px 16px', borderRadius: 'var(--radius-sm)', marginBottom: '18px' }}>
          {error}
        </div>
      )}

      {message && (
        <div className="hl-fade-up" style={{ color: 'var(--up)', background: 'rgba(0,245,160,0.07)', border: '1px solid rgba(0,245,160,0.28)', padding: '12px 16px', borderRadius: 'var(--radius-sm)', marginBottom: '18px' }}>
          ✓ {message.text}
          {message.safety && (
            <div style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '5px' }}>
              Poprzedni stan zapisano jako <strong>{message.safety}</strong> — można go przywrócić z listy poniżej.
            </div>
          )}
        </div>
      )}

      {/* Akcje */}
      <div className="hl-panel" style={{ padding: '20px', marginBottom: '22px' }}>
        <div style={{ display: 'flex', gap: '11px', flexWrap: 'wrap', alignItems: 'center' }}>
          <button onClick={handleExport} disabled={busy === 'export'} className="hl-btn hl-btn-primary">
            {busy === 'export' ? 'Przygotowuję…' : '⬇ Pobierz kopię zapasową'}
          </button>

          <label className="hl-btn" style={{ cursor: busy === 'import' ? 'not-allowed' : 'pointer', display: 'inline-block' }}>
            {busy === 'import' ? 'Wczytuję…' : '⬆ Wczytaj z pliku'}
            <input type="file" accept=".json" onChange={handleImport} disabled={busy === 'import'} style={{ display: 'none' }} />
          </label>

          <button onClick={handleSnapshot} disabled={busy === 'snapshot'} className="hl-btn">
            {busy === 'snapshot' ? 'Zapisuję…' : '📌 Zrób migawkę na serwerze'}
          </button>

          {busy && <Spinner size={20} />}
        </div>

        <div style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '12px' }}>
          Wczytanie pliku <strong>nadpisuje</strong> bieżące dane, ale wcześniej automatycznie zapisuje
          migawkę obecnego stanu — pomyłkowy import zawsze da się cofnąć.
        </div>
      </div>

      {/* Co jest w danych */}
      <h3 style={{ fontSize: '15px', marginBottom: '12px', color: 'var(--text-muted)' }}>Zawartość</h3>
      {loading && !status ? (
        <div style={{ color: 'var(--text-dim)' }}>Sprawdzam…</div>
      ) : (
        <div style={{ display: 'flex', gap: '11px', flexWrap: 'wrap', marginBottom: '28px' }}>
          {Object.entries(status?.items || {}).map(([key, item]) => (
            <div
              key={key}
              className="hl-panel"
              style={{ flex: '1 1 175px', padding: '13px 16px', opacity: item.exists ? 1 : 0.5 }}
            >
              <div style={{ fontSize: '12.5px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                {LABELS[key] || key}
              </div>
              <div className="hl-num" style={{ fontSize: '21px', fontWeight: 700, color: item.exists ? 'var(--text)' : 'var(--text-dim)' }}>
                {item.count}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-dim)', marginTop: '3px' }}>
                {item.exists ? `${item.size_kb} kB · ${item.modified}` : 'brak pliku'}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Migawki na serwerze */}
      <h3 style={{ fontSize: '15px', marginBottom: '12px', color: 'var(--text-muted)' }}>
        Migawki na serwerze {status?.snapshots?.length ? `(${status.snapshots.length})` : ''}
      </h3>

      {status?.snapshots?.length === 0 ? (
        <div style={{ color: 'var(--text-dim)', fontSize: '13px' }}>
          Brak migawek. Powstaną automatycznie przy pierwszym imporcie albo po kliknięciu „Zrób migawkę".
        </div>
      ) : (
        <table className="hl-table">
          <thead>
            <tr>
              <th>Nazwa</th>
              <th>Utworzona</th>
              <th>Rozmiar</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(status?.snapshots || []).map((s) => (
              <tr key={s.name}>
                <td className="hl-num" style={{ fontSize: '12.5px' }}>{s.name}</td>
                <td>{s.created}</td>
                <td className="hl-num">{s.size_kb} kB</td>
                <td style={{ textAlign: 'right' }}>
                  {confirmRestore === s.name ? (
                    <span style={{ display: 'inline-flex', gap: '7px', alignItems: 'center' }}>
                      <span style={{ color: 'var(--warn)', fontSize: '12px' }}>Nadpisze dane. Na pewno?</span>
                      <button onClick={() => handleRestore(s.name)} className="hl-btn hl-btn-sm hl-btn-primary">
                        Tak, przywróć
                      </button>
                      <button onClick={() => setConfirmRestore(null)} className="hl-btn hl-btn-sm">
                        Anuluj
                      </button>
                    </span>
                  ) : (
                    <span style={{ display: 'inline-flex', gap: '7px' }}>
                      <button
                        onClick={() => setConfirmRestore(s.name)}
                        disabled={busy === s.name}
                        className="hl-btn hl-btn-sm"
                      >
                        {busy === s.name ? 'Przywracam…' : 'Przywróć'}
                      </button>
                      <button onClick={() => handleDeleteSnapshot(s.name)} className="hl-btn hl-btn-sm hl-btn-danger">
                        Usuń
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '11px', marginTop: '22px' }}>
        Migawki leżą w <strong>backend/backups/</strong>. Są na tym samym dysku co dane, więc przy awarii
        dysku nie pomogą — pobrany plik trzymaj gdzieś indziej (chmura, pendrive).
      </p>
    </div>
  )
}

export default BackupPanel
