import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion, useScroll, useTransform } from 'framer-motion'
import SpeakerTrack from './SpeakerTrack.jsx'
import { speakers as mockSpeakers, transcript as mockTranscript } from '../data/mockMeeting'
import { useMotionPreference } from '../context/MotionPreferenceContext.jsx'

// A tall wrapper (300vh) with a sticky inner viewport: as the user scrolls
// vertically through that extra height, real transcript rows translate
// horizontally across the screen instead of stacking down the page --
// the section stays pinned until the horizontal scroll finishes, then
// releases into normal vertical scroll again.
export default function HorizontalTranscript() {
  const { reduceMotion } = useMotionPreference()
  const wrapRef = useRef(null)
  const { scrollYProgress } = useScroll({ target: wrapRef, offset: ['start start', 'end end'] })
  const x = useTransform(scrollYProgress, [0, 1], ['2%', '-72%'])

  const speakerFor = (id) => mockSpeakers.find((s) => s.id === id)

  return (
    <section
      id="transcript"
      className={`showcase-section is-alt horizontal-wrap ${reduceMotion ? 'is-static' : ''}`}
      ref={wrapRef}
    >
      {/* Laser cymatics / sound-wave visualization, CC BY 4.0 (Wikimedia
          Commons) -- lightened from its near-black original (blended
          toward white + resaturated) before being saved into this repo. */}
      <div className="ambient-bg" style={{ backgroundImage: 'url(/images/home-bg-transcript.jpg)' }} aria-hidden="true" />
      <div className="ambient-bg-scrim" aria-hidden="true" />
      <div className="horizontal-sticky">
        <div className="horizontal-head">
          <p className="eyebrow">Transcript</p>
          <h2 className="showcase-title">Every line, labeled by speaker.</h2>
          <p className="hero-sub" style={{ color: 'rgba(255,255,255,0.7)' }}>
            No one enrolls their voice in advance, keep scrolling to read the real sample, line by line.
          </p>
          <Link to="/transcript" className="link-inline feature-cta">See the full transcript →</Link>
        </div>
        <motion.div className="horizontal-track" style={reduceMotion ? {} : { x }}>
          {mockTranscript.slice(0, 6).map((seg) => (
            <div className="horizontal-card" key={seg.id}>
              <SpeakerTrack segment={seg} speaker={speakerFor(seg.speakerId)} />
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}
