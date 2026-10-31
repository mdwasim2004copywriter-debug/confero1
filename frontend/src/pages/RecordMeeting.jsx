import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { formatTimestamp } from '../data/mockMeeting'
import { useAuth } from '../context/AuthContext.jsx'
import { useRecording } from '../context/RecordingContext.jsx'

const MEETING_TYPES = [
  'General',
  'Corporate',
  'Freelance / Client',
  'Webinar / Lecture',
  'Interview',
  'Personal',
]

export default function RecordMeeting() {
  // Pre-start configuration only -- once Start is clicked these values are
  // captured by RecordingContext for the rest of that session's lifetime,
  // so losing this local state on navigation is fine, nothing in-progress
  // depends on it. Everything in-progress lives in RecordingContext
  // instead (see that file) specifically so it survives this page
  // unmounting/remounting when the user navigates away and back.
  const [fileName, setFileName] = useState(null)
  const [includeVideo, setIncludeVideo] = useState(false)
  const [meetingType, setMeetingType] = useState(MEETING_TYPES[0])
  const [mixedLanguage, setMixedLanguage] = useState(false)

  const videoPreviewRef = useRef(null)

  const navigate = useNavigate()
  const { isLoggedIn } = useAuth()
  const {
    recording,
    paused,
    seconds,
    status,
    errorMsg,
    hasVideoTrack,
    liveCaption,
    interimCaption,
    captionSource,
    streamCounts,
    streamRef,
    streamActiveRef,
    startRecording,
    startTabCapture,
    stopRecording,
    restartRecording,
    pauseRecording,
    resumeRecording,
    uploadRecording,
  } = useRecording()

  const locked = recording || status === 'processing'

  // Re-attaches the live preview to whichever <video> element this
  // component instance currently owns -- covers both the initial start
  // and returning to this page while a recording (held in
  // RecordingContext, not here) is still active.
  useEffect(() => {
    if (recording && hasVideoTrack && videoPreviewRef.current && streamRef.current) {
      videoPreviewRef.current.srcObject = streamRef.current
      videoPreviewRef.current.play().catch(() => {})
    }
    return () => {
      if (videoPreviewRef.current) videoPreviewRef.current.srcObject = null
    }
  }, [recording, hasVideoTrack, streamRef])

  function handleUpload(e) {
    const file = e.target.files?.[0]
    if (file) {
      setFileName(file.name)
      uploadRecording(file, meetingType, mixedLanguage)
    }
  }

  return (
    <div className="page page-record">
      <p className="eyebrow">Capture</p>
      <h1>Start a meeting</h1>

      {!isLoggedIn && (
        <p className="guest-note">
          Recording as a guest, this won't be saved to an account.{' '}
          <button type="button" className="link-inline" onClick={() => navigate('/login')}>
            Log in / sign up
          </button>{' '}
          to save it to Your Meetings.
        </p>
      )}

      <div className="meeting-type-select">
        <p className="meeting-type-label">What sort of meeting is this?</p>
        <div className="meeting-type-options">
          {MEETING_TYPES.map((t) => (
            <button
              type="button"
              key={t}
              className={`meeting-type-chip ${meetingType === t ? 'is-active' : ''}`}
              onClick={() => setMeetingType(t)}
              disabled={locked}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <label className="video-toggle">
        <input
          type="checkbox"
          checked={mixedLanguage}
          onChange={(e) => setMixedLanguage(e.target.checked)}
          disabled={locked}
        />
        Speakers switch languages mid-recording (e.g. Tamil-English), slower but more accurate
      </label>

      <div className="record-panel">
        {!recording && (
          <label className="video-toggle">
            <input
              type="checkbox"
              checked={includeVideo}
              onChange={(e) => setIncludeVideo(e.target.checked)}
              disabled={status === 'processing'}
            />
            Include video (camera)
          </label>
        )}

        <div className={`video-preview-wrap ${recording && hasVideoTrack ? '' : 'is-hidden'}`}>
          <video ref={videoPreviewRef} autoPlay muted playsInline className="video-preview" />
          <div className="video-preview-badge">
            <span className="record-dial-dot" aria-hidden="true" />
            {formatTimestamp(seconds)}
          </div>
        </div>
        {!(recording && hasVideoTrack) && (
          <div className={`record-dial ${recording ? 'is-recording' : ''}`}>
            <span className="record-dial-dot" aria-hidden="true" />
            <span className="record-dial-time">{formatTimestamp(seconds)}</span>
            <span className="record-dial-label">
              {status === 'processing' ? 'Processing…' : recording ? (paused ? 'Paused' : 'Listening…') : 'Ready'}
            </span>
          </div>
        )}

        {recording && (
          <div className="live-caption">
            <p className="live-caption-label">
              Live caption · {paused ? 'paused' : captionSource === 'speech' ? 'online' : 'offline (local)'}
            </p>
            <p className="live-caption-text">
              {liveCaption}
              {interimCaption && <span className="live-caption-interim"> {interimCaption}</span>}
              {!liveCaption && !interimCaption && (
                <span className="live-caption-placeholder">
                  {paused ? 'Paused' : 'Listening for speech…'}
                </span>
              )}
            </p>
            {streamActiveRef.current && (
              <p className="live-caption-label">
                Streaming transcription: {streamCounts.uploaded} chunk{streamCounts.uploaded === 1 ? '' : 's'} transcribed
                {streamCounts.failed > 0 && `, ${streamCounts.failed} failed (will still finalize with a gap)`}
              </p>
            )}
          </div>
        )}

        <div className="record-actions">
          <button
            type="button"
            className={recording ? 'btn btn-danger' : 'btn btn-primary'}
            onClick={recording ? stopRecording : () => startRecording({ includeVideo, meetingType, mixedLanguage })}
            disabled={status === 'processing'}
          >
            {recording ? 'Stop recording' : 'Start recording'}
          </button>

          {recording && (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={paused ? resumeRecording : pauseRecording}
              disabled={status === 'processing'}
            >
              {paused ? 'Resume' : 'Pause'}
            </button>
          )}

          {recording && (
            <button type="button" className="btn btn-ghost" onClick={restartRecording} disabled={status === 'processing'}>
              Restart
            </button>
          )}

          {!recording && (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => startTabCapture({ meetingType, mixedLanguage })}
              disabled={status === 'processing'}
            >
              Capture a tab (e.g. Google Meet)
            </button>
          )}

          <label className="btn btn-ghost upload-btn">
            Upload audio or video file
            <input type="file" accept="audio/*,video/*" onChange={handleUpload} hidden disabled={status === 'processing'} />
          </label>
        </div>

        {!recording && (
          <p className="hero-sub">
            To capture a Google Meet call live: open it in another tab first, click "Capture a tab" above, then
            pick that tab and make sure "Share tab audio" is checked in the picker (Chrome/Edge on desktop only).
            Already have a Meet recording from Drive? Upload it directly with the button above.
          </p>
        )}

        {fileName && <p className="upload-filename">Selected: {fileName}</p>}

        {status === 'processing' && (
          <p className="status-banner">Transcribing and generating notes, this can take a minute on CPU…</p>
        )}
        {status === 'error' && errorMsg && <p className="status-banner status-error">{errorMsg}</p>}
      </div>
    </div>
  )
}
