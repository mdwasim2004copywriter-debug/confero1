// "Processing stats" panel shown right after a recording finishes --
// category, media type, size/duration, and a breakdown of where the
// backend's processing time actually went. Renders nothing when there's
// no metrics (the sample/mock view, or a meeting recorded before this
// feature existed), so it's purely additive.

const SOURCE_LABELS = { mic: 'Live · Mic', tab: 'Live · Tab capture', upload: 'Upload' }

const STAGES = [
  { key: 'decode', label: 'Decode', color: 'var(--moss)' },
  { key: 'transcription', label: 'Transcription', color: 'var(--teal)' },
  { key: 'diarization', label: 'Diarization', color: 'var(--periwinkle)' },
  { key: 'notesGeneration', label: 'Notes', color: 'var(--coral)' },
]

export function formatBytes(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function MetricsPanel({ metrics }) {
  if (!metrics) return null
  const { source, mediaType, fileSizeBytes, audioDurationSeconds, timingSeconds = {} } = metrics
  const total = timingSeconds.total || STAGES.reduce((sum, s) => sum + (timingSeconds[s.key] || 0), 0) || 1

  return (
    <div className="metrics-panel">
      <div className="metrics-panel-head">
        <span className="metrics-pill">{SOURCE_LABELS[source] || 'Uncategorized'}</span>
        <span className="metrics-pill metrics-pill-muted">{mediaType === 'video' ? 'Video' : 'Audio'}</span>
        <span className="metrics-stat">{formatBytes(fileSizeBytes)}</span>
        <span className="metrics-stat">{Math.round(audioDurationSeconds || 0)}s audio</span>
        <span className="metrics-stat">{total.toFixed(1)}s to process</span>
      </div>
      <div className="timing-bar">
        {STAGES.map((s) => {
          const seconds = timingSeconds[s.key] || 0
          if (seconds <= 0) return null
          return (
            <div
              key={s.key}
              className="timing-bar-segment"
              style={{ flexBasis: `${(seconds / total) * 100}%`, background: s.color }}
              title={`${s.label}: ${seconds}s`}
            />
          )
        })}
      </div>
      <div className="timing-bar-legend">
        {STAGES.map((s) => (
          <span key={s.key} className="timing-legend-item">
            <span className="timing-legend-dot" style={{ background: s.color }} />
            {s.label} {(timingSeconds[s.key] || 0).toFixed(1)}s
          </span>
        ))}
      </div>
    </div>
  )
}
