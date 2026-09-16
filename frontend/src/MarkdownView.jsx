import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Wspólny renderer odpowiedzi AI. Gemini zwraca Markdown (nagłówki, pogrubienia,
 * listy, tabele), więc zamiast wyświetlać surowe gwiazdki i hasztagi - renderujemy je
 * z porządnym stylowaniem dopasowanym do ciemnego motywu aplikacji.
 */
function MarkdownView({ children }) {
  return (
    <div style={{ color: '#e8e8ec', lineHeight: '1.7', fontSize: '14px' }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h2 style={{ color: 'var(--text)', fontSize: '19px', margin: '20px 0 10px', borderBottom: '1px solid #3a3a4a', paddingBottom: '6px' }}>{children}</h2>
          ),
          h2: ({ children }) => (
            <h3 style={{ color: 'var(--accent)', fontSize: '16px', margin: '22px 0 8px', fontWeight: 600 }}>{children}</h3>
          ),
          h3: ({ children }) => (
            <h4 style={{ color: 'var(--cyan)', fontSize: '14px', margin: '16px 0 6px', fontWeight: 600 }}>{children}</h4>
          ),
          p: ({ children }) => <p style={{ margin: '0 0 12px' }}>{children}</p>,
          strong: ({ children }) => <strong style={{ color: 'var(--text)', fontWeight: 700 }}>{children}</strong>,
          em: ({ children }) => <em style={{ color: '#9a9aa8' }}>{children}</em>,
          ul: ({ children }) => <ul style={{ margin: '0 0 12px', paddingLeft: '22px' }}>{children}</ul>,
          ol: ({ children }) => <ol style={{ margin: '0 0 12px', paddingLeft: '22px' }}>{children}</ol>,
          li: ({ children }) => <li style={{ margin: '0 0 6px' }}>{children}</li>,
          hr: () => <hr style={{ border: 'none', borderTop: '1px solid #33333f', margin: '18px 0' }} />,
          code: ({ inline, children }) =>
            inline ? (
              <code style={{ background: '#2a2a38', padding: '2px 6px', borderRadius: '4px', fontSize: '13px', color: '#7FD6A0' }}>{children}</code>
            ) : (
              <pre style={{ background: '#15151f', padding: '12px', borderRadius: '6px', overflowX: 'auto', fontSize: '13px' }}>
                <code>{children}</code>
              </pre>
            ),
          blockquote: ({ children }) => (
            <blockquote style={{ borderLeft: '3px solid #4FA3FF', margin: '0 0 12px', padding: '4px 0 4px 14px', color: '#b8b8c4' }}>{children}</blockquote>
          ),
          table: ({ children }) => (
            <div style={{ overflowX: 'auto', margin: '0 0 14px' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '13px' }}>{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th style={{ border: '1px solid #3a3a4a', padding: '8px 10px', background: '#252532', textAlign: 'left', color: 'var(--text)' }}>{children}</th>
          ),
          td: ({ children }) => <td style={{ border: '1px solid #3a3a4a', padding: '8px 10px' }}>{children}</td>,
        }}
      >
        {children || ''}
      </ReactMarkdown>
    </div>
  )
}

export default MarkdownView