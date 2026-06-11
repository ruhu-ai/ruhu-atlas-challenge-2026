import type { ArtifactDisambiguationCandidate, ChatMessage } from './widget-types'

function isArtifactDisambiguationMessage(
  message: ChatMessage,
): message is ChatMessage & {
  metadata: {
    message_type: 'artifact_disambiguation'
    payload: {
      artifact_type?: string
      candidates?: ArtifactDisambiguationCandidate[]
    }
  }
} {
  return (
    message.role === 'assistant'
    && message.metadata?.message_type === 'artifact_disambiguation'
    && Boolean(message.metadata?.payload)
  )
}

export function getArtifactDisambiguationCandidates(message: ChatMessage): ArtifactDisambiguationCandidate[] {
  if (!isArtifactDisambiguationMessage(message)) {
    return []
  }
  const rawCandidates = message.metadata.payload?.candidates
  if (!Array.isArray(rawCandidates)) {
    return []
  }
  return rawCandidates.filter((candidate): candidate is ArtifactDisambiguationCandidate => {
    return (
      Boolean(candidate)
      && typeof candidate.artifact_id === 'string'
      && typeof candidate.artifact_type === 'string'
      && typeof candidate.title === 'string'
      && typeof candidate.status === 'string'
    )
  })
}

interface ArtifactDisambiguationCardProps {
  message: ChatMessage
  onSelect: (candidate: ArtifactDisambiguationCandidate) => void
}

export function ArtifactDisambiguationCard({
  message,
  onSelect,
}: ArtifactDisambiguationCardProps) {
  const candidates = getArtifactDisambiguationCandidates(message)
  if (candidates.length === 0) {
    return null
  }

  return (
    <div
      style={{
        marginTop: '10px',
        display: 'grid',
        gap: '8px',
      }}
    >
      {candidates.map((candidate) => (
        <button
          key={candidate.artifact_id}
          type="button"
          onClick={() => onSelect(candidate)}
          style={{
            width: '100%',
            borderRadius: '12px',
            border: '1px solid rgba(148, 163, 184, 0.5)',
            background: '#fff',
            padding: '10px 12px',
            textAlign: 'left',
            cursor: 'pointer',
          }}
        >
          <div style={{ fontSize: '0.875rem', fontWeight: 600, color: '#0f172a' }}>
            {candidate.title}
          </div>
          <div style={{ fontSize: '0.75rem', color: '#475569', marginTop: '2px' }}>
            {candidate.status.replace(/_/g, ' ')}
            {candidate.external_id ? ` · ${candidate.external_id}` : ''}
          </div>
        </button>
      ))}
    </div>
  )
}
