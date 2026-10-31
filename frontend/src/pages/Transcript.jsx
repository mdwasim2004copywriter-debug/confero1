import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import SpeakerTrack from '../components/SpeakerTrack.jsx'
import MetricsPanel from '../components/MetricsPanel.jsx'
import { useAuth, authFetch } from '../context/AuthContext.jsx'
import { useMeeting } from '../context/MeetingContext.jsx'
import { API_BASE } from '../config/api.js'
import { meeting as mockMeeting, speakers as mockSpeakers, transcript as mockTranscript } from '../data/mockMeeting'

export default function Transcript() {
  const { liveMeeting, setLiveMeeting, updateGuestMeetingSpeakers } = useMeeting()
  const { token } = useAuth()
  const isLive = Boolean(liveMeeting)

  const meeting = liveMeeting?.meeting ?? mockMeeting
  const transcript = liveMeeting?.transcript ?? mockTranscript

  // Owned here (not read fresh from liveMeeting/mock on every render) so a
  // rename can update every line from that speaker at once -- previously
  // each SpeakerTrack kept its own local copy of the label, so renaming
  // one line left every other line from the same person unchanged.
  const [speakers, setSpeakers] = useState(liveMeeting?.speakers ?? mockSpeakers)
  useEffect(() => {
    setSpeakers(liveMeeting?.speakers ?? mockSpeakers)
  }, [liveMeeting])

  const speakerFor = (id) => speakers.find((s) => s.id === id)

  const handleRename = (speakerId, newLabel) => {
    const updatedSpeakers = speakers.map((s) =>
      s.id === speakerId ? { ...s, label: newLabel, labelSource: 'manual' } : s
    )
    setSpeakers(updatedSpeakers)

    if (!liveMeeting) return // sample/demo data -- nothing to persist

    setLiveMeeting({ ...liveMeeting, speakers: updatedSpeakers })
    updateGuestMeetingSpeakers(liveMeeting.meeting.id, updatedSpeakers)

    if (token) {
      // Best-effort: already applied above regardless of whether this
      // call succeeds, so a flaky network just means the next reload
      // falls back to whatever the backend still has.
      authFetch(`${API_BASE}/meetings/${liveMeeting.meeting.id}/speakers/${speakerId}`, token, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: newLabel }),
      }).catch(() => {})
    }
  }

  return (
    <div className="page page-transcript">
      <div className="page-head-row">
        <div>
          <p className="eyebrow">{isLive ? 'Your recording · Transcript' : 'Sample output · Transcript'}</p>
          <h1>{meeting.title}</h1>
          <p className="hero-sub">
            {meeting.date} · {meeting.speakerCount} speaker{meeting.speakerCount === 1 ? '' : 's'} · exact, speaker-labeled
            transcript. Click any speaker label to rename them.
          </p>
        </div>
        <Link to="/notes" className="btn btn-primary">View meeting notes →</Link>
      </div>

      <MetricsPanel metrics={liveMeeting?.metrics} />

      <div className="transcript-list">
        {transcript.map((seg) => (
          <SpeakerTrack
            key={seg.id}
            segment={seg}
            speaker={speakerFor(seg.speakerId)}
            editable
            onRename={(newLabel) => handleRename(seg.speakerId, newLabel)}
          />
        ))}
      </div>
    </div>
  )
}