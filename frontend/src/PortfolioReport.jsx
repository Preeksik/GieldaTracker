import { useState } from 'react'

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
    <div style={{ color: '#fff' }}>
      {/* Sekcja 1: Dywersyfikacja */}
      <h2 style={{ marginBottom: '10px' }}>📊 Analiza dywersyfikacji portfela</h2>
      <p style={{ color: '#aaa', marginBottom: '20px', maxWidth: '700px' }}>
        Sprawdza koncentrację w sektorach, krajach i walutach, wskazuje luki i sugeruje
        konkretne kierunki rebalansowania.
      </p>

      <button
        onClick={runDiversification}
        disabled={diversificationLoading}
        style={{
          padding: '12px 24px',
          background: '#007BFF',
          color: 'white',
          border: 'none',
          borderRadius: '5px',
          cursor: diversificationLoading ? 'not-allowed' : 'pointer',
          fontSize: '15px',
          marginBottom: '20px',
        }}
      >
        {diversificationLoading ? 'Analizuję dywersyfikację...' : '🔍 Sprawdź dywersyfikację'}
      </button>

      {diversificationError && (
        <div style={{ color: '#FF5252', marginBottom: '20px', background: '#3a1f1f', padding: '12px', borderRadius: '6px' }}>
          {diversificationError}
        </div>
      )}

      {diversification && (
        <div
          style={{
            background: '#333',
            padding: '20px',
            borderRadius: '8px',
            borderLeft: '4px solid #007BFF',
            marginBottom: '30px',
          }}
        >
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.7', margin: 0, color: '#eee' }}>{diversification}</p>
        </div>
      )}

      <hr style={{ border: 'none', borderTop: '1px solid #444', margin: '30px 0' }} />

      {/* Sekcja 2: Pytania o portfel */}
      <h2 style={{ marginBottom: '10px' }}>💬 Zapytaj o swój portfel</h2>
      <p style={{ color: '#aaa', marginBottom: '20px', maxWidth: '700px' }}>
        Np. "w co zainwestować dodatkowe 5k na IKE - dokupić coś co już mam, czy szukać
        czegoś nowego?". Odpowiedzi bazują na realnych danych Twojego portfela i pamiętają
        wcześniejsze pytania w tej rozmowie.
      </p>

      {thread.map((t, idx) => (
        <div key={idx} style={{ marginBottom: '16px' }}>
          <div style={{ color: '#007BFF', fontWeight: 'bold', marginBottom: '6px' }}>❓ {t.question}</div>
          <div
            style={{
              background: '#333',
              padding: '15px',
              borderRadius: '8px',
              borderLeft: '4px solid #555',
            }}
          >
            <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', margin: 0, color: '#eee' }}>{t.answer}</p>
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
            borderRadius: '5px',
            border: '1px solid #ccc',
            color: '#fff',
            backgroundColor: '#333',
          }}
        />
        <button
          onClick={askQuestion}
          disabled={questionLoading || !question.trim()}
          style={{
            padding: '12px 24px',
            background: '#007BFF',
            color: 'white',
            border: 'none',
            borderRadius: '5px',
            cursor: questionLoading ? 'not-allowed' : 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          {questionLoading ? 'Pytam...' : 'Zapytaj'}
        </button>
      </div>

      {questionError && (
        <div style={{ color: '#FF5252', marginTop: '12px' }}>{questionError}</div>
      )}

      <p style={{ color: '#777', fontSize: '12px', marginTop: '25px' }}>
        ⚠️ To automatycznie generowana analiza edukacyjna, nie porada inwestycyjna.
      </p>
    </div>
  )
}

export default PortfolioReport