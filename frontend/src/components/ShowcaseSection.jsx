import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion, useScroll, useTransform } from 'framer-motion'
import { useMotionPreference } from '../context/MotionPreferenceContext.jsx'

// One dark, cinematic section: real screenshot of the actual app page as
// a crisp, contained, softly-glowing panel, with copy alongside it. Each
// gets its own entrance animation for the panel (variant), while the
// copy always uses a simple, reliably legible fade+lift. `parallax` adds
// continuous foreground/background drift while the section is in view --
// the panel moves further than the text, a real (not just entrance-only)
// depth cue.
const PANEL_VARIANTS = {
  scale: {
    hidden: { opacity: 0, scale: 0.82 },
    visible: { opacity: 1, scale: 1, transition: { duration: 0.8, ease: 'easeOut' } },
  },
  'slide-up': {
    hidden: { opacity: 0, y: 110 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.7, ease: 'easeOut' } },
  },
  fade: {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { duration: 0.9, ease: 'easeInOut' } },
  },
  'slide-side': (reversed) => ({
    hidden: { opacity: 0, x: reversed ? -110 : 110 },
    visible: { opacity: 1, x: 0, transition: { duration: 0.7, ease: 'easeOut' } },
  }),
}

export default function ShowcaseSection({ id, eyebrow, title, ctaTo, ctaLabel, image, imageWidth, imageHeight, reversed = false, variant = 'scale', alt = false, parallax = false, large = false, ambientImage = null }) {
  const { reduceMotion } = useMotionPreference()
  const sectionRef = useRef(null)

  const resolved = typeof PANEL_VARIANTS[variant] === 'function' ? PANEL_VARIANTS[variant](reversed) : PANEL_VARIANTS[variant]
  const panelVariants = reduceMotion ? { hidden: {}, visible: {} } : resolved

  const textVariants = {
    hidden: reduceMotion ? {} : { opacity: 0, y: 34 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.6, ease: 'easeOut' } },
  }

  const { scrollYProgress } = useScroll({ target: sectionRef, offset: ['start end', 'end start'] })
  const panelParallaxY = useTransform(scrollYProgress, [0, 1], parallax && !reduceMotion ? [150, -150] : [0, 0])
  const textParallaxY = useTransform(scrollYProgress, [0, 1], parallax && !reduceMotion ? [40, -40] : [0, 0])

  return (
    <section
      ref={sectionRef}
      id={id}
      className={`showcase-section ${reversed ? 'is-reversed' : ''} ${alt ? 'is-alt' : ''} ${large ? 'is-large' : ''}`}
    >
      {ambientImage && (
        <>
          <div className="ambient-bg" style={{ backgroundImage: `url(${ambientImage})` }} aria-hidden="true" />
          <div className="ambient-bg-scrim" aria-hidden="true" />
        </>
      )}
      <div className="showcase-grid">
        <motion.div
          className="showcase-copy"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: false, amount: 0.4 }}
          variants={textVariants}
          style={{ y: textParallaxY }}
        >
          <p className="eyebrow">{eyebrow}</p>
          <h2 className="showcase-title">{title}</h2>
          <Link to={ctaTo} className="link-inline feature-cta">{ctaLabel} →</Link>
        </motion.div>
        <div className="showcase-panel-wrap">
          <motion.div
            className="showcase-panel"
            initial="hidden"
            whileInView="visible"
            viewport={{ once: false, amount: 0.3 }}
            variants={panelVariants}
            style={{ y: panelParallaxY }}
          >
            <img
              src={image}
              alt={`${title}, sample screenshot`}
              width={imageWidth}
              height={imageHeight}
              style={{ aspectRatio: imageWidth && imageHeight ? `${imageWidth} / ${imageHeight}` : undefined }}
            />
          </motion.div>
        </div>
      </div>
    </section>
  )
}
