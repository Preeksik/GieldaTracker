import { useState } from 'react'
import MarkdownView from './MarkdownView'
import { StepLoader, InlineLoader } from './Loader'

const API_URL = 'http://127.0.0.1:8000'

function PortfolioReport() {
  // Analiza dywersyfikacji
  const [diversification, setDiversification] = useState('')
  const [diversificationLoading, setDiversificationLoading] = useState(false)
  const [diversificationError, setDiversificationError] = useState('')

  // Wątek pytań o cały portfel
  const [thread, setThread] = useState([]) // [{question, answer}]
  const [question, setQuestion] = useState('')
  const [questionLoading, setQuestionLoading] = useState(false)
  const [questionError, setQuestionError] = useState('')

  const runDiversification = async () => {
    setDiversificationLoading(true)
    setDiversificationError('')
    setDiversification('')
    try {
      const res = await fetch(`${API_URL}/api/portfolio/diversification`)
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się wygenerować analizy dywersyfikacji.')
      }
      const data = await res.json()
      setDiversification(data.report)
    } catch (err) {
      setDiversificationError(err.message)
    } finally {
      setDiversificationLoading(false)
    }
  }

  const askQuestion = async () => {
    if (!question.trim()) return
    setQuestionLoading(true)
    setQuestionError('')

    // Kontekst dla AI: analiza dywersyfikacji (jeśli była) + cały dotychczasowy wątek pytań
    const contextParts = []
    if (diversification) {
      contextParts.push(`Wcześniejsza analiza dywersyfikacji:\n${diversification}`)
    }
    thread.forEach((t) => {
      contextParts.push(`Pytanie: ${t.question}\nOdpowiedź: ${t.answer}`)
    })
    const previousAnalysis = contextParts.join('\n\n')

    try {
      const res = await fetch(`${API_URL}/api/portfolio/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, previous_analysis: previousAnalysis }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(errData?.detail || 'Nie udało się uzyskać odpowiedzi.')
      }
      const data = await res.json()
      setThread([...thread, { question, answer: data.answer }])
      setQuestion('')
    } catch (err) {
      setQuestionError(err.message)
    } finally {
      setQuestionLoading(false)
    }
  }

  return (
    <div style={{ color: 'var(--text)' }}>
      {/* Sekcja 1: Dywersyfikacja */}
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>📊 Analiza dywersyfikacji portfela</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Sprawdza koncentrację w sektorach, krajach i walutach, wskazuje luki i sugeruje
        konkretne kierunki rebalansowania.
      </p>

      <button
        onClick={runDiversification}
        disabled={diversificationLoading} className="hl-btn hl-btn-primary"
        style={{
          padding: '12px 24px',
          background: 'var(--accent)',
          color: 'white',
          border: 'none',
          borderRadius: 'var(--radius-sm)',
          cursor: diversificationLoading ? 'not-allowed' : 'pointer',
          fontSize: '15px',
          marginBottom: '20px',
        }}
      >
        {diversificationLoading ? 'Analizuję dywersyfikację...' : '🔍 Sprawdź dywersyfikację'}
      </button>

      {diversificationLoading && (
        <div style={{ marginBottom: '20px' }}>
          <StepLoader
            title="Analizuję strukturę portfela"
            steps={['Pobieram aktualne wyceny pozycji', 'Ustalam sektory, branże i kraje', 'Liczę wagi i koncentrację ryzyka', 'Gemini szuka luk i układa plan']}
          />
        </div>
      )}

      {diversificationError && (
        <div style={{ color: 'var(--down)', marginBottom: '20px', background: 'rgba(255,91,127,0.08)', padding: '12px', borderRadius: '6px' }}>
          {diversificationError}
        </div>
      )}

      {diversification && (
        <div
          style={{
            background: 'var(--bg-elevated)',
            padding: '20px',
            borderRadius: 'var(--radius)',
            borderLeft: '4px solid var(--accent)',
            marginBottom: '30px',
          }}
        >
          <MarkdownView>{diversification}</MarkdownView>
        </div>
      )}

      <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '30px 0' }} />

      {/* Sekcja 2: Pytania o portfel */}
      <h2 style={{ marginBottom: '10px', fontSize: '20px', letterSpacing: '-0.3px' }}>💬 Zapytaj o swój portfel</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '700px' }}>
        Np. "w co zainwestować dodatkowe 5k na IKE - dokupić coś co już mam, czy szukać
        czegoś nowego?". Odpowiedzi bazują na realnych danych Twojego portfela i pamiętają
        wcześniejsze pytania w tej rozmowie.
      </p>

      {thread.map((t, idx) => (
        <div key={idx} style={{ marginBottom: '16px' }}>
          <div style={{ color: 'var(--accent)', fontWeight: 'bold', marginBottom: '6px' }}>❓ {t.question}</div>
          <div
            style={{
              background: 'var(--bg-elevated)',
              padding: '15px',
              borderRadius: 'var(--radius)',
              borderLeft: '4px solid var(--border-bright)',
            }}
          >
            <MarkdownView>{t.answer}</MarkdownView>
          </div>
        </div>
      ))}

      <div style={{ display: 'flex', gap: '10px', marginTop: '10px' }}>
        <input
          placeholder='np. "w co zainwestować dodatkowe 5k na IKE?"'
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && askQuestion()}
          style={{
            flex: 1,
            padding: '12px',
            fontSize: '15px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border)',
            color: 'var(--text)',
            backgroundColor: 'var(--bg-elevated)',
          }}
        />
        <button
          onClick={askQuestion}
          disabled={questionLoading || !question.trim()} className="hl-btn hl-btn-primary"
          style={{
            padding: '12px 24px',
            background: 'var(--accent)',
            color: 'white',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            cursor: questionLoading ? 'not-allowed' : 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          {questionLoading ? 'Myślę…' : 'Zapytaj ▸'}
        </button>
      </div>

      {questionError && (
        <div style={{ color: 'var(--down)', marginTop: '12px' }}>{questionError}</div>
      )}

      <p style={{ color: 'var(--text-dim)', fontSize: '12px', marginTop: '25px' }}>
        ⚠️ To automatycznie generowana analiza edukacyjna, nie porada inwestycyjna.
      </p>
    </div>
  )
}

export default PortfolioReport