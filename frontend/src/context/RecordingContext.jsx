import { createContext, useContext, useRef, useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMeeting } from './MeetingContext.jsx'
import { useAuth, authFetch } from './AuthContext.jsx'
import { API_BASE } from '../config/api.js'
import * as chunkQueue from '../lib/chunkQueue.js'

// Holds the live, in-progress recording session -- the MediaRecorder/
// MediaStream refs, the Phase B streaming-chunk capture loop, live
// captions, and upload/finalize state -- so it survives navigating away
// from and back to /record. This used to live as useState/useRef inside
// RecordMeeting.jsx itself, which meant react-router unmounting that page
// silently leaked the live mic stream and orphaned the streaming session:
// nothing stopped it, and nothing could reattach to it on return. Lifting
// it here (mounted once, in App.jsx, above <Routes>) fixes that without
// touching any chunk timing, retry, merge, or backend logic below.
//
// Session-start-time choices (meetingType/mixedLanguage/includeVideo) are
// NOT lifted here, only genuinely in-progress state is. They're passed
// into startRecording() once and captured in sessionConfigRef for the
// rest of that session's lifetime.

const RecordingContext = createContext(null)

const API_URL = `${API_BASE}/process-meeting`
const LIVE_URL = `${API_BASE}/live-transcribe`
const LIVE_CHUNK_MS = 5000

// --- Phase B: streaming transcription during the recording -----------
// Unchanged from the original RecordMeeting.jsx -- see that file's prior
// history for why these specific values/comments exist.
const STREAMING_ENABLED = true
const STREAM_START_URL = `${API_BASE}/stream/start`
const STREAM_CHUNK_URL = `${API_BASE}/stream/chunk`
const STREAM_FINALIZE_URL = `${API_BASE}/stream/finalize`
// Must match backend/streaming.py's CHUNK_SECONDS/OVERLAP_SECONDS -- the
// server doesn't enforce these (it trusts each chunk's own reported
// start_time/end_time), but the merge quality depends on client and
// server agreeing on roughly the same overlap window.
const STREAM_CHUNK_SECONDS = 10
const STREAM_OVERLAP_SECONDS = 1.5
const STREAM_MAX_RETRY = 5
const STREAM_FINALIZE_DRAIN_TIMEOUT_MS = 20000

function extensionFor(mimeType = '') {
  if (mimeType.includes('webm')) return 'webm'
  if (mimeType.includes('ogg')) return 'ogg'
  if (mimeType.includes('mp4')) return mimeType.startsWith('video/') ? 'mp4' : 'm4a'
  return 'webm'
}

export function RecordingProvider({ children }) {
  const [recording, setRecording] = useState(false)
  const [paused, setPaused] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [status, setStatus] = useState('idle') // idle | processing | error
  const [errorMsg, setErrorMsg] = useState(null)
  const [hasVideoTrack, setHasVideoTrack] = useState(false)
  const [liveCaption, setLiveCaption] = useState('')
  const [interimCaption, setInterimCaption] = useState('')
  const [captionSource, setCaptionSource] = useState(null) // 'speech' | 'chunks' | null
  const [streamCounts, setStreamCounts] = useState({ uploaded: 0, failed: 0 })

  const intervalRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const chunksRef = useRef([])
  const streamRef = useRef(null)
  const recordingRef = useRef(false) // mirrors `recording` state for use inside async/event callbacks
  const pausedRef = useRef(false) // mirrors `paused` state, same reason
  const recognitionRef = useRef(null)
  const restartRequestedRef = useRef(false)
  const chunkRecorderRef = useRef(null)
  const chunkTimeoutRef = useRef(null)
  const chunkUploadBusyRef = useRef(false)

  // --- Phase B streaming refs ---
  const streamIdRef = useRef(null)
  const streamActiveRef = useRef(false) // true only once /api/stream/start has actually succeeded
  const streamChunkRecordersRef = useRef([]) // up to 2 concurrently active, due to overlap
  const streamRecordingStartRef = useRef(0) // performance.now() at recording start, chunk times are relative to this
  // Pause/resume: paused time must be excised from the timeline the
  // streaming chunks report, not reported as a gap -- no audio exists for
  // that span (the recorder is genuinely stopped/paused during it), and a
  // reported gap is not something the merge/finalize backend code (left
  // untouched here) has ever had to handle. effectiveNowMs() below always
  // subtracts the running total, so resuming looks, to every downstream
  // consumer, like recording never paused at all.
  const totalPausedMsRef = useRef(0)
  const pauseStartedAtRef = useRef(0)
  const nextChunkSeqRef = useRef(0) // next sequence number scheduleChunk should use, kept current so resume can pick up where pause left off
  const legacyBlobRef = useRef(null) // the whole-file blob, always captured in parallel, used only as the fallback
  const uploadDrainBusyRef = useRef(false)
  const sessionConfigRef = useRef({ meetingType: 'General', mixedLanguage: false })
  // True for a tab/display capture session (see startTabCapture below) --
  // forces startLiveCaptions to skip the Web Speech API, which always
  // listens to the real OS microphone and has no way to point itself at
  // an arbitrary MediaStream. For a mic recording that's harmless (it's
  // listening to the same mic being recorded); for a captured Meet tab it
  // would silently caption the room mic instead of the call.
  const forceChunkCaptionsRef = useRef(false)

  const navigate = useNavigate()
  const { setLiveMeeting, addGuestMeeting } = useMeeting()
  const { isLoggedIn, token } = useAuth()

  useEffect(() => {
    if (recording && !paused) {
      intervalRef.current = setInterval(() => setSeconds((s) => s + 1), 1000)
    } else {
      clearInterval(intervalRef.current)
    }
    return () => clearInterval(intervalRef.current)
  }, [recording, paused])

  function effectiveNowMs() {
    return performance.now() - streamRecordingStartRef.current - totalPausedMsRef.current
  }

  async function uploadRecording(blobOrFile, filename, meetingType, mixedLanguage, source = 'upload', includeVideo = false) {
    setStatus('processing')
    setErrorMsg(null)
    try {
      const form = new FormData()
      const name = filename || `recording.${extensionFor(blobOrFile.type)}`
      form.append('audio', blobOrFile, name)
      form.append('title', 'Live Recording')
      form.append('meeting_type', meetingType)
      form.append('multilingual', mixedLanguage ? 'true' : 'false')
      form.append('source', source)
      form.append('include_video', includeVideo ? 'true' : 'false')

      const res = await authFetch(API_URL, token, { method: 'POST', body: form })
      if (!res.ok) throw new Error(`Server responded with ${res.status}`)

      const data = await res.json()
      if (data.error) {
        setErrorMsg(data.error)
        setStatus('error')
        return
      }

      setLiveMeeting(data)
      if (!isLoggedIn) addGuestMeeting(data) // backend never saved this one, keep it locally so History can show it
      setStatus('idle')
      navigate('/transcript')
    } catch (err) {
      setErrorMsg(
        `Could not reach the backend (${err.message}). Is the API server running? ` +
          `In backend/, with the venv active: uvicorn api:app --reload --port 8000`
      )
      setStatus('error')
    }
  }

  // --- Live captions while recording -----------------------------------
  async function uploadLiveChunk(blob) {
    if (chunkUploadBusyRef.current) return
    chunkUploadBusyRef.current = true
    try {
      const form = new FormData()
      form.append('audio', blob, `chunk.${extensionFor(blob.type)}`)
      form.append('multilingual', sessionConfigRef.current.mixedLanguage ? 'true' : 'false')
      const res = await fetch(LIVE_URL, { method: 'POST', body: form })
      if (!res.ok) return
      const data = await res.json()
      if (data.text) {
        setLiveCaption((prev) => (prev ? `${prev} ${data.text}` : data.text))
      }
    } catch {
      // best-effort only
    } finally {
      chunkUploadBusyRef.current = false
    }
  }

  function startChunkCaptions(stream) {
    setCaptionSource('chunks')

    function recordNextChunk() {
      if (!recordingRef.current || pausedRef.current) return
      const rec = new MediaRecorder(stream)
      const localChunks = []
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) localChunks.push(e.data)
      }
      rec.onstop = () => {
        const blob = new Blob(localChunks, { type: rec.mimeType || 'audio/webm' })
        uploadLiveChunk(blob)
        if (recordingRef.current && !pausedRef.current) recordNextChunk()
      }
      chunkRecorderRef.current = rec
      rec.start()
      chunkTimeoutRef.current = setTimeout(() => rec.stop(), LIVE_CHUNK_MS)
    }

    recordNextChunk()
  }

  // --- Phase B: streaming transcription during recording ----------------
  async function uploadOneChunk(chunk) {
    try {
      const form = new FormData()
      form.append('audio', chunk.blob, `chunk_${chunk.sequenceNumber}.${extensionFor(chunk.blob.type)}`)
      form.append('stream_id', chunk.streamId)
      form.append('sequence_number', String(chunk.sequenceNumber))
      form.append('start_time', String(chunk.startTime))
      form.append('end_time', String(chunk.endTime))
      const res = await fetch(STREAM_CHUNK_URL, { method: 'POST', body: form })
      if (!res.ok) return false
      const data = await res.json()
      return data.status === 'ok' || data.replayed === true
    } catch {
      return false
    }
  }

  async function drainUploadQueue() {
    if (uploadDrainBusyRef.current) return
    uploadDrainBusyRef.current = true
    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const pending = await chunkQueue.getPendingChunks(streamIdRef.current, STREAM_MAX_RETRY)
        if (pending.length === 0) break
        const chunk = pending[0]
        const ok = await uploadOneChunk(chunk)
        if (ok) {
          await chunkQueue.deleteChunk(chunk.id)
          setStreamCounts((c) => ({ ...c, uploaded: c.uploaded + 1 }))
        } else {
          await chunkQueue.markChunkFailed(chunk.id)
          const nextRetryCount = chunk.retryCount + 1
          if (nextRetryCount >= STREAM_MAX_RETRY) {
            setStreamCounts((c) => ({ ...c, failed: c.failed + 1 }))
            continue
          }
          const backoffMs = Math.min(1000 * 2 ** nextRetryCount, 16000)
          await new Promise((r) => setTimeout(r, backoffMs))
        }
      }
    } finally {
      uploadDrainBusyRef.current = false
    }
  }

  async function enqueueAndUpload(seq, startTime, endTime, blob) {
    await chunkQueue.enqueueChunk({ streamId: streamIdRef.current, sequenceNumber: seq, startTime, endTime, blob })
    drainUploadQueue() // fire-and-forget: capture never waits on upload
  }

  function scheduleChunk(stream, seq) {
    if (!recordingRef.current || !streamActiveRef.current || pausedRef.current) return
    nextChunkSeqRef.current = seq + 1
    const rec = new MediaRecorder(stream)
    const localChunks = []
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) localChunks.push(e.data)
    }
    streamChunkRecordersRef.current.push(rec)
    rec.start()
    const actualStartMs = effectiveNowMs()
    rec.onstop = () => {
      streamChunkRecordersRef.current = streamChunkRecordersRef.current.filter((r) => r !== rec)
      if (localChunks.length === 0) return // nothing captured, nothing to upload
      const blob = new Blob(localChunks, { type: rec.mimeType || 'audio/webm' })
      const endTime = effectiveNowMs() / 1000
      enqueueAndUpload(seq, actualStartMs / 1000, endTime, blob)
    }

    const chunkLengthMs = STREAM_CHUNK_SECONDS * 1000
    const overlapMs = STREAM_OVERLAP_SECONDS * 1000
    const nextStartDelay = Math.max(chunkLengthMs - overlapMs, 1000)
    setTimeout(() => {
      if (recordingRef.current && streamActiveRef.current && !pausedRef.current) {
        scheduleChunk(stream, seq + 1)
      }
    }, nextStartDelay)
    setTimeout(() => {
      if (rec.state === 'recording') rec.stop()
    }, chunkLengthMs)
  }

  function startStreamingChunks(stream) {
    streamRecordingStartRef.current = performance.now()
    totalPausedMsRef.current = 0
    nextChunkSeqRef.current = 0
    scheduleChunk(stream, 0)
  }

  function stopStreamingChunks() {
    streamChunkRecordersRef.current.forEach((rec) => {
      if (rec.state === 'recording') rec.stop() // onstop still fires -- the final partial chunk is captured, not dropped
    })
    streamChunkRecordersRef.current = []
  }

  async function finishStreaming() {
    setStatus('processing')
    setErrorMsg(null)
    try {
      const deadline = Date.now() + STREAM_FINALIZE_DRAIN_TIMEOUT_MS
      while (Date.now() < deadline) {
        const counts = await chunkQueue.countByStatus(streamIdRef.current)
        if (counts.pending === 0) break
        await new Promise((r) => setTimeout(r, 500))
      }

      const form = new FormData()
      form.append('stream_id', streamIdRef.current)
      const res = await authFetch(STREAM_FINALIZE_URL, token, { method: 'POST', body: form })
      if (!res.ok) throw new Error(`finalize responded ${res.status}`)
      const data = await res.json()
      if (data.error) throw new Error(data.error)

      await chunkQueue.clearStream(streamIdRef.current)
      streamActiveRef.current = false
      streamIdRef.current = null

      setLiveMeeting(data)
      if (!isLoggedIn) addGuestMeeting(data)
      setStatus('idle')
      navigate('/transcript')
    } catch (err) {
      console.warn('Streaming finalize failed, falling back to whole-file upload:', err)
      streamActiveRef.current = false
      if (legacyBlobRef.current) {
        uploadRecording(
          legacyBlobRef.current,
          undefined,
          sessionConfigRef.current.meetingType,
          sessionConfigRef.current.mixedLanguage,
          sessionConfigRef.current.source,
          sessionConfigRef.current.includeVideo
        )
      } else {
        setErrorMsg('Lost the streamed transcript and no fallback recording was available. Please try again.')
        setStatus('error')
      }
    }
  }

  function startLiveCaptions(stream) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    const isMobile = navigator.userAgentData?.mobile ?? /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)

    if (
      forceChunkCaptionsRef.current ||
      !SpeechRecognition ||
      !navigator.onLine ||
      isMobile ||
      sessionConfigRef.current.mixedLanguage
    ) {
      startChunkCaptions(stream)
      return
    }

    const recognition = new SpeechRecognition()
    recognition.continuous = true
    recognition.interimResults = true

    recognition.onresult = (event) => {
      let finalText = ''
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        if (result.isFinal) finalText += result[0].transcript
        else interim += result[0].transcript
      }
      if (finalText.trim()) {
        setLiveCaption((prev) => (prev ? `${prev} ${finalText.trim()}` : finalText.trim()))
      }
      setInterimCaption(interim.trim())
    }

    recognition.onerror = (event) => {
      if (event.error === 'no-speech') return
      recognitionRef.current = null
      setInterimCaption('')
      if (recordingRef.current && !pausedRef.current) startChunkCaptions(stream)
    }

    recognition.onend = () => {
      if (recordingRef.current && !pausedRef.current && recognitionRef.current === recognition) {
        try {
          recognition.start()
        } catch {
          // already starting/started
        }
      }
    }

    recognitionRef.current = recognition
    setCaptionSource('speech')
    recognition.start()
  }

  function stopLiveCaptions() {
    if (recognitionRef.current) {
      const recognition = recognitionRef.current
      recognitionRef.current = null
      recognition.onend = null
      recognition.stop()
    }
    if (chunkTimeoutRef.current) {
      clearTimeout(chunkTimeoutRef.current)
      chunkTimeoutRef.current = null
    }
    if (chunkRecorderRef.current?.state === 'recording') {
      chunkRecorderRef.current.stop()
    }
    chunkRecorderRef.current = null
    setInterimCaption('')
    setCaptionSource(null)
  }

  // Everything below is source-agnostic: it doesn't matter whether `stream`
  // came from the real microphone (startRecording) or a captured browser
  // tab (startTabCapture), the recorder/captions/streaming-chunk/finalize
  // pipeline is identical either way. `restart` is called from the
  // restart-requested branch below instead of hardcoding which start
  // function to call again, so restartRecording() keeps working for
  // either source without duplicating this whole method.
  function beginSession(stream, { meetingType, mixedLanguage, source, includeVideo, restart }) {
    streamRef.current = stream
    forceChunkCaptionsRef.current = source === 'tab'

    const videoTrack = stream.getVideoTracks().length > 0
    setHasVideoTrack(videoTrack)

    const recorder = new MediaRecorder(stream)
    chunksRef.current = []

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data)
    }
    recorder.onstop = () => {
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
      if (restartRequestedRef.current) {
        restartRequestedRef.current = false
        restart()
        return
      }
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
      if (streamActiveRef.current) {
        legacyBlobRef.current = blob
      } else {
        uploadRecording(blob, undefined, meetingType, mixedLanguage, source, includeVideo)
      }
    }

    mediaRecorderRef.current = recorder
    recorder.start()
    setSeconds(0)
    setLiveCaption('')
    setInterimCaption('')
    setStreamCounts({ uploaded: 0, failed: 0 })
    legacyBlobRef.current = null
    pausedRef.current = false
    setPaused(false)
    recordingRef.current = true
    setRecording(true)
    startLiveCaptions(stream)

    if (STREAMING_ENABLED) {
      ;(async () => {
        try {
          const form = new FormData()
          form.append('title', 'Live Recording')
          form.append('meeting_type', meetingType)
          form.append('multilingual', mixedLanguage ? 'true' : 'false')
          form.append('source', source)
          form.append('include_video', includeVideo ? 'true' : 'false')
          const res = await authFetch(STREAM_START_URL, token, { method: 'POST', body: form })
          if (!res.ok) return
          const data = await res.json()
          if (!data.stream_id || !recordingRef.current) return
          streamIdRef.current = data.stream_id
          streamActiveRef.current = true
          startStreamingChunks(stream)
        } catch {
          // offline/unreachable at record-start -- streaming never activates this session
        }
      })()
    }
  }

  async function startRecording({ includeVideo, meetingType, mixedLanguage }) {
    if (recordingRef.current) return // already recording -- never start a second session
    setErrorMsg(null)
    sessionConfigRef.current = { meetingType, mixedLanguage, source: 'mic', includeVideo }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        video: includeVideo,
      })
      beginSession(stream, {
        meetingType,
        mixedLanguage,
        source: 'mic',
        includeVideo,
        restart: () => startRecording({ includeVideo, meetingType, mixedLanguage }),
      })
    } catch (err) {
      setErrorMsg(
        includeVideo
          ? 'Could not access your camera/microphone. Check your browser’s camera & mic permission for this site and try again.'
          : 'Could not access your microphone. Check your browser’s mic permission for this site and try again.'
      )
    }
  }

  // Captures another browser tab's audio (e.g. a Google Meet call open in
  // a second tab) instead of the real microphone, and feeds it into the
  // exact same recording/streaming/finalize pipeline as startRecording
  // above. Chrome/Edge desktop only -- tab-audio sharing isn't supported
  // in Firefox/Safari or on mobile browsers.
  async function startTabCapture({ meetingType, mixedLanguage }) {
    if (recordingRef.current) return
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setErrorMsg(
        'Capturing a tab needs a Chromium browser (Chrome or Edge) on desktop -- this browser doesn’t support it.'
      )
      return
    }
    setErrorMsg(null)
    sessionConfigRef.current = { meetingType, mixedLanguage, source: 'tab', includeVideo: false }
    try {
      // `video: true` is required by getDisplayMedia to show the picker at
      // all, even though the video track is discarded immediately below --
      // only the tab's audio is ever recorded/sent anywhere.
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })

      if (stream.getAudioTracks().length === 0) {
        stream.getTracks().forEach((t) => t.stop())
        setErrorMsg(
          'No audio was shared. In the picker, choose "Chrome Tab" (the tab with your Google Meet call) and make sure "Share tab audio" is checked.'
        )
        return
      }

      stream.getVideoTracks().forEach((t) => {
        t.stop()
        stream.removeTrack(t)
      })

      // Clicking the browser's own "Stop sharing" bar (instead of
      // Confero's Stop button) ends the track on its own -- treat that
      // exactly like pressing Stop, not like an error/hang.
      stream.getAudioTracks()[0].addEventListener('ended', () => {
        if (recordingRef.current) stopRecording()
      })

      beginSession(stream, {
        meetingType,
        mixedLanguage,
        source: 'tab',
        includeVideo: false,
        restart: () => startTabCapture({ meetingType, mixedLanguage }),
      })
    } catch (err) {
      setErrorMsg(
        'Could not capture tab audio. Choose "Chrome Tab" and check "Share tab audio" in the picker (Chrome/Edge desktop only).'
      )
    }
  }

  function restartRecording() {
    if (!recordingRef.current) return
    restartRequestedRef.current = true
    pausedRef.current = false
    setPaused(false)
    stopLiveCaptions() // the restart() callback (called from onstop below, via beginSession) starts fresh captions of its own
    stopStreamingChunks()
    if (streamActiveRef.current) {
      chunkQueue.clearStream(streamIdRef.current)
      streamActiveRef.current = false
      streamIdRef.current = null
    }
    mediaRecorderRef.current?.stop() // recorder.onstop sees the flag above and calls restart() instead of uploading
  }

  function stopRecording() {
    recordingRef.current = false
    pausedRef.current = false
    setPaused(false)
    mediaRecorderRef.current?.stop()
    stopLiveCaptions()
    stopStreamingChunks()
    setRecording(false)
    if (streamActiveRef.current) {
      finishStreaming()
    }
  }

  // Toggles recording/paused. Deliberately distinct from stopRecording:
  // the microphone/recorder/session all stay alive and nothing is
  // finalized -- only new capture is paused.
  function pauseRecording() {
    if (!recordingRef.current || pausedRef.current) return
    pausedRef.current = true
    setPaused(true)
    pauseStartedAtRef.current = performance.now()
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.pause() // native pause: no dataavailable/stop fires, nothing lost, nothing uploaded early
    }
    // Cleanly finalizes whatever chunk was mid-capture (its onstop still
    // fires and uploads it, same as a normal chunk boundary) and prevents
    // the next one from being scheduled via the pausedRef guard above.
    stopStreamingChunks()
    stopLiveCaptions()
  }

  function resumeRecording() {
    if (!recordingRef.current || !pausedRef.current) return
    totalPausedMsRef.current += performance.now() - pauseStartedAtRef.current
    pausedRef.current = false
    setPaused(false)
    if (mediaRecorderRef.current?.state === 'paused') {
      mediaRecorderRef.current.resume()
    }
    if (streamActiveRef.current && streamRef.current) {
      scheduleChunk(streamRef.current, nextChunkSeqRef.current)
    }
    if (streamRef.current) startLiveCaptions(streamRef.current)
  }

  function uploadFile(file, meetingType, mixedLanguage) {
    uploadRecording(file, file.name, meetingType, mixedLanguage, 'upload', false)
  }

  const value = {
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
    uploadRecording: uploadFile,
  }

  return <RecordingContext.Provider value={value}>{children}</RecordingContext.Provider>
}

export function useRecording() {
  const ctx = useContext(RecordingContext)
  if (!ctx) {
    throw new Error('useRecording must be used inside <RecordingProvider>')
  }
  return ctx
}
