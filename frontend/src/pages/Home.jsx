import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { motion, useScroll, useTransform } from 'framer-motion'
import Lenis from 'lenis'
import ShowcaseSection from '../components/ShowcaseSection.jsx'
import HorizontalTranscript from '../components/HorizontalTranscript.jsx'
import { notes as mockNotes } from '../data/mockMeeting'
import { useMeeting } from '../context/MeetingContext.jsx'
import { useAuth, authFetch } from '../context/AuthContext.jsx'
import { useMotionPreference } from '../context/MotionPreferenceContext.jsx'
import { API_BASE } from '../config/api.js'

function formatDuration(totalSeconds) {
  const mins = Math.round(totalSeconds / 60)
  return mins < 1 ? '<1 min' : `${mins} min`
}

// Smooth (Lenis) scrolling, active only while the cinematic pitch is
// mounted -- every other route in the app keeps plain native scroll. The
// raf loop stops entirely while the tab is in the background (switched
// away, minimized, screen off) so it isn't burning CPU/battery for a page
// nobody is looking at -- resumes the moment the tab is visible again.
function useLenisScroll() {
  const { reduceMotion } = useMotionPreference()
  const lenisRef = useRef(null)

  useEffect(() => {
    if (reduceMotion) return undefined
    const lenis = new Lenis({ duration: 1.1, smoothWheel: true })
    lenisRef.current = lenis
    let raf = null
    function loop(time) {
      lenis.raf(time)
      raf = requestAnimationFrame(loop)
    }
    function start() {
      if (raf === null) raf = requestAnimationFrame(loop)
    }
    function stop() {
      if (raf !== null) {
        cancelAnimationFrame(raf)
        raf = null
      }
    }
    function onVisibilityChange() {
      if (document.hidden) stop()
      else start()
    }
    start()
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      stop()
      lenis.destroy()
      lenisRef.current = null
    }
  }, [reduceMotion])

  return lenisRef
}

function CinematicHero({ heroRef }) {
  const { reduceMotion } = useMotionPreference()
  const videoRef = useRef(null)
  const durationRef = useRef(0)

  const { scrollYProgress } = useScroll({ target: heroRef, offset: ['start start', 'end start'] })
  const videoScale = useTransform(scrollYProgress, [0, 1], [1, reduceMotion ? 1 : 1.35])

  // Scroll-driven video: once the user starts leaving the hero, playback
  // stops being ambient and is scrubbed directly by scroll position
  // instead. Resumes normal autoplay if scrolled back to the very top.
  useEffect(() => {
    const v = videoRef.current
    if (!v || reduceMotion) return undefined
    const onLoaded = () => { durationRef.current = v.duration || 0 }
    v.addEventListener('loadedmetadata', onLoaded)
    const unsub = scrollYProgress.on('change', (latest) => {
      const duration = durationRef.current
      if (!duration) return
      if (latest > 0.02) {
        if (!v.paused) v.pause()
        v.currentTime = Math.min(duration - 0.05, latest * duration)
      } else if (v.paused) {
        v.play().catch(() => {})
      }
    })
    return () => {
      v.removeEventListener('loadedmetadata', onLoaded)
      unsub()
    }
  }, [scrollYProgress, reduceMotion])

  // Decoding a looping video costs real battery on a phone -- stop it
  // outright when the tab is backgrounded, not just when scrolled away.
  useEffect(() => {
    const v = videoRef.current
    if (!v) return undefined
    function onVisibilityChange() {
      if (document.hidden) v.pause()
      else if (scrollYProgress.get() <= 0.02) v.play().catch(() => {})
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [scrollYProgress])

  const heroStagger = { hidden: {}, visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.12 } } }
  const heroItem = {
    hidden: reduceMotion ? {} : { opacity: 0, y: 34 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.55, ease: 'easeOut' } },
  }
  // One-time mask reveal on mount -- a circular wipe opening over the
  // video, not scroll-triggered, so it's not dependent on any scroll
  // library timing (reliable regardless of Lenis init order).
  const maskReveal = {
    hidden: { clipPath: 'circle(0% at 50% 45%)' },
    visible: { clipPath: 'circle(75% at 50% 45%)', transition: { duration: 1.5, ease: [0.65, 0, 0.35, 1] } },
  }

  return (
    <section ref={heroRef} className="cine-section cine-hero" id="top">
      <motion.div
        className="cine-hero-mask"
        initial={reduceMotion ? false : 'hidden'}
        animate="visible"
        variants={maskReveal}
      >
        <motion.video
          ref={videoRef}
          className="cine-hero-video"
          src="/videos/mic-waveform.mp4"
          autoPlay
          muted
          loop
          playsInline
          aria-hidden="true"
          style={{ scale: videoScale }}
        />
      </motion.div>
      <div className="cine-hero-scrim" />
      <motion.div className="cine-inner" initial="hidden" animate="visible" variants={heroStagger}>
        <motion.p className="eyebrow" variants={heroItem}>Voice intelligence for meetings</motion.p>
        <motion.h1 variants={heroItem}>
          Every voice in the room,
          <br />
          labeled, transcribed, understood.
        </motion.h1>
        <motion.p className="hero-sub" variants={heroItem}>
          Confero listens to a meeting, tells each person's voice apart,
          keeps an exact record of what they said, and turns it into notes
          and a report you can send anywhere.
        </motion.p>
        <motion.div className="hero-actions" variants={heroItem}>
          <Link to="/record" className="btn btn-primary">Start a meeting</Link>
        </motion.div>
      </motion.div>
    </section>
  )
}

function NotesTypography() {
  const decisions = mockNotes.decisions.slice(0, 4)
  const { reduceMotion } = useMotionPreference()
  const listContainer = { hidden: {}, visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.18 } } }
  const listItem = {
    hidden: reduceMotion ? {} : { opacity: 0, x: -44 },
    visible: { opacity: 1, x: 0, transition: { duration: 0.5, ease: 'easeOut' } },
  }
  const panelVariants = {
    hidden: reduceMotion ? {} : { opacity: 0, scale: 0.92 },
    visible: { opacity: 1, scale: 1, transition: { duration: 0.7, ease: 'easeOut' } },
  }

  return (
    <section id="notes" className="showcase-section">
      {/* Colorful sticky notes, CC BY-SA 2.0 (Wikimedia Commons). */}
      <div className="ambient-bg" style={{ backgroundImage: 'url(/images/home-bg-notes.jpg)' }} aria-hidden="true" />
      <div className="ambient-bg-scrim" aria-hidden="true" />
      <div className="showcase-grid">
        <div className="showcase-copy">
          <motion.div
            initial={reduceMotion ? false : { opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: false, amount: 0.4 }}
            transition={{ duration: 0.55, ease: 'easeOut' }}
          >
            <p className="eyebrow">Notes</p>
            <h2 className="showcase-title">Decisions, not guesswork.</h2>
          </motion.div>
          <motion.ul
            className="type-lines notes-type-list"
            initial="hidden"
            whileInView="visible"
            viewport={{ once: false, amount: 0.3 }}
            variants={listContainer}
          >
            {decisions.map((d, i) => (
              <motion.li key={i} variants={listItem} className="notes-type-item">
                <span className="decision-text">{d.text}</span>
                <span className="decision-speaker">, {d.speaker}</span>
              </motion.li>
            ))}
          </motion.ul>
          <Link to="/notes" className="link-inline feature-cta">See sample notes →</Link>
        </div>
        <div className="showcase-panel-wrap">
          <motion.div
            className="showcase-panel"
            initial="hidden"
            whileInView="visible"
            viewport={{ once: false, amount: 0.3 }}
            variants={panelVariants}
          >
            <img
              src="/images/preview-notes.png"
              alt="Sample notes screenshot"
              width={1680}
              height={623}
              style={{ aspectRatio: '1680 / 623' }}
            />
          </motion.div>
        </div>
      </div>
    </section>
  )
}

function CinematicPitch() {
  const heroRef = useRef(null)

  // Keeps the smooth-scroll feel for the hero's video-scrub/parallax
  // effects; no longer needs its return value since there's no in-page
  // nav driving scrollTo anymore.
  useLenisScroll()

  return (
    <div className="home-cinematic">
      <CinematicHero heroRef={heroRef} />

      {/* ambientImage: studio microphone, CC0/public domain (Wikimedia Commons). */}
      <ShowcaseSection
        id="record"
        eyebrow="Capture"
        title="Record from your mic."
        ctaTo="/record"
        ctaLabel="Try recording"
        image="/images/preview-record.png"
        imageWidth={1668}
        imageHeight={777}
        variant="scale"
        parallax
        ambientImage="/images/home-bg-record.jpg"
      />

      <HorizontalTranscript />

      <NotesTypography />

      {/* ambientImage: colorful paper clips, CC BY 2.0 (Wikimedia Commons). */}
      <ShowcaseSection
        id="reports"
        alt
        large
        eyebrow="Reports"
        title="A clean report, ready to share."
        ctaTo="/reports"
        ctaLabel="See a sample report"
        image="/images/preview-reports.png"
        imageWidth={2004}
        imageHeight={1122}
        variant="scale"
        ambientImage="/images/home-bg-reports.jpg"
      />
    </div>
  )
}

export default function Home() {
  const [realMeetings, setRealMeetings] = useState(null) // null = not loaded yet, [] = loaded but empty
  const { loadMeetingById } = useMeeting()
  const { isLoggedIn, token } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (!isLoggedIn) {
      setRealMeetings(null)
      return
    }
    let cancelled = false

    function fetchMeetings() {
      authFetch(`${API_BASE}/meetings`, token)
        .then((res) => (res.ok ? res.json() : Promise.reject()))
        .then((data) => {
          if (!cancelled) setRealMeetings(data)
        })
        .catch(() => {
          if (!cancelled) setRealMeetings(null) // backend not running, or token expired, fall back to sample cards
        })
    }

    fetchMeetings()

    // Cross-device sync: a meeting recorded on another device while this
    // dashboard is open elsewhere shows up here without a manual refresh.
    // Only polls while the tab is actually visible.
    let interval = null
    function startPolling() {
      if (interval === null) interval = setInterval(fetchMeetings, 8000)
    }
    function stopPolling() {
      if (interval !== null) {
        clearInterval(interval)
        interval = null
      }
    }
    function onVisibilityChange() {
      if (document.hidden) stopPolling()
      else startPolling()
    }
    startPolling()
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelled = true
      stopPolling()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [isLoggedIn, token])

  async function openMeeting(id) {
    try {
      await loadMeetingById(id)
      navigate('/transcript')
    } catch (err) {
      console.error(err)
    }
  }

  const hasRealMeetings = Array.isArray(realMeetings) && realMeetings.length > 0

  // Logged in: this is the app now, not a pitch for it, just your
  // meetings and the one thing you'd come here to do.
  if (isLoggedIn) {
    return (
      <div className="page page-home">
        <div className="page-head-row">
          <h1>Your meetings</h1>
          <Link to="/record" className="btn btn-primary">Record a meeting</Link>
        </div>

        {hasRealMeetings ? (
          <div className="meeting-grid">
            {realMeetings.map((m) => (
              <button type="button" key={m.id} className="meeting-card" onClick={() => openMeeting(m.id)}>
                <p className="meeting-card-title">{m.title}</p>
                <p className="meeting-card-meta">
                  {m.date} · {m.speakerCount} speaker{m.speakerCount === 1 ? '' : 's'} · {formatDuration(m.durationSeconds)}
                </p>
              </button>
            ))}
          </div>
        ) : (
          <p className="hero-sub">
            {realMeetings === null
              ? 'Once your backend is running, your meetings will show up here.'
              : 'No recordings yet, record your first meeting to get started.'}
          </p>
        )}
      </div>
    )
  }

  // Logged out: the cinematic pitch, for people who haven't tried it yet.
  return <CinematicPitch />
}
