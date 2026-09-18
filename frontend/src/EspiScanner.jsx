import { useState } from 'react'
import MarkdownView from './MarkdownView'
import { StepLoader } from './Loader'

const API_URL = 'http://127.0.0.1:8000'

function EspiScanner() {
  const [report, setReport] = useState('')
  const [companiesChecked, setCompaniesChecked] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const runScan = async () => {
    setLoading(true)
    setError('')
    setReport('')
    try {
      const res = await fetch(`${API_URL}/api/espi/scan`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się przeskanować komunikatów ESPI/EBI.')
      }
      const data = await res.json()
      setReport(data.report)
      setCompaniesChecked(data.companies_checked || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ color: 'var(--text)' }}>
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>📜 Skaner komunikatów ESPI/EBI</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Sprawdza świeże raporty bieżące spółek z portfela i watchlisty (umowy, WZA, zmiany
        w zarządzie, wyniki), i dla każdego znalezionego komunikatu daje 2-3 zdaniowe
        podsumowanie z oceną wpływu na kurs.
      </p>

      <button
        onClick={runScan}
        disabled={loading} className="hl-btn hl-btn-primary"
        style={{
          padding: '12px 24px',
          background: 'var(--accent)',
          color: 'white',
          border: 'none',
          borderRadius: 'var(--radius-sm)',
          cursor: loading ? 'not-allowed' : 'pointer',
          fontSize: '15px',
          marginBottom: '20px',
        }}
      >
        {loading ? 'Skanuję komunikaty... (może potrwać do minuty)' : '📡 Skanuj komunikaty'}
      </button>

      {loading && (
        <div style={{ marginBottom: '20px' }}>
          <StepLoader
            title="Skanuję ESPI/EBI"
            steps={[
              'Zbieram spółki z portfela i watchlisty',
              'Szukam świeżych komunikatów bieżących',
              'Odsiewam newsy od realnych raportów',
              'Gemini streszcza i ocenia wpływ na kurs',
            ]}
          />
        </div>
      )}

      {error && (
        <div style={{ color: 'var(--down)', marginBottom: '20px', background: 'rgba(255,91,127,0.08)', padding: '12px', borderRadius: '6px' }}>
          {error}
        </div>
      )}

      {report && (
        <div
          style={{
            background: 'var(--bg-elevated)',
            padding: '20px',
            borderRadius: 'var(--radius)',
            borderLeft: '4px solid var(--accent)',
            marginBottom: '16px',
          }}
        >
          <MarkdownView>{report}</MarkdownView>
        </div>
      )}

      {companiesChecked.length > 0 && (
        <p style={{ color: 'var(--text-dim)', fontSize: '12px' }}>
          Sprawdzone spółki: {companiesChecked.join(', ')}
        </p>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '12px' }}>
        ⚠️ Źródło: publiczny kanał Google News RSS zawężony do Bankier.pl (przedrukowuje treść
        komunikatów spółek), nie oficjalne API GPW/KNF. To analiza edukacyjna, nie porada inwestycyjna.
      </p>
    </div>
  )
}

export default EspiScanner
