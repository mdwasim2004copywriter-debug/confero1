import { useState } from 'react'
import { formatTimestamp } from '../data/mockMeeting'

// Renders one diarized line: a colored "track" for the speaker (stands in
// for that speaker's waveform), a click-to-rename label, the timestamp,
// and the exact text that was said. This is the shape every future
// diarization + transcription result will be rendered through.
//
// The label itself is controlled by the parent (via `speaker.label` +
// `onRename`), not owned here -- every SpeakerTrack for the same speaker
// needs to update together when one of them is renamed, which isn't
// possible if each line keeps its own local copy of the label.
export default function SpeakerTrack({ segment, speaker, editable = false, onRename }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const label = speaker?.label ?? 'Unknown speaker'

  const commit = () => {
    setEditing(false)
    const trimmed = draft.trim()
    if (trimmed && trimmed !== label) {
      onRename?.(trimmed)
    }
  }

  const startEditing = () => {
    setDraft(label)
    setEditing(true)
  }

  return (
    <div className="speaker-track" style={{ '--speaker-color': speaker?.color ?? 'var(--muted)' }}>
      <div className="speaker-track-bar" aria-hidden="true">
        <span className="bar b1" />
        <span className="bar b2" />
        <span className="bar b3" />
        <span className="bar b4" />
        <span className="bar b5" />
      </div>

      <div className="speaker-track-body">
        <div className="speaker-track-meta">
          {editable && editing ? (
            <input
              className="speaker-label-input"
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => e.key === 'Enter' && commit()}
            />
          ) : (
            <button
              type="button"
              className="speaker-label"
              onClick={() => editable && startEditing()}
              title={editable ? 'Click to rename this speaker' : undefined}
            >
              {label}
            </button>
          )}
          {speaker?.labelSource === 'detected' && (
            <span className="speaker-label-badge" title="Name detected automatically from the recording — click the label to correct it">
              auto
            </span>
          )}
          <span className="speaker-timestamp">
            {formatTimestamp(segment.start)}–{formatTimestamp(segment.end)}
          </span>
        </div>
        <p className="speaker-text">{segment.text}</p>
      </div>
    </div>
  )
}
