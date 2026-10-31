import jsPDF from 'jspdf'
import { useMeeting } from '../context/MeetingContext.jsx'
import { meeting as mockMeeting, notes as mockNotes, speakers as mockSpeakers } from '../data/mockMeeting'

function formatDuration(totalSeconds) {
  if (!totalSeconds && totalSeconds !== 0) return null
  const mins = Math.round(totalSeconds / 60)
  return mins < 1 ? '<1 min' : `${mins} min`
}

// window.print() only opens the browser's print dialog -- getting an actual
// PDF file still depends on the user manually picking "Save as PDF" there,
// which doesn't read as a real download. This builds the same report as a
// real PDF (text, not a screenshot, so it stays searchable/selectable) and
// saves it directly, mirroring the on-screen sections 1:1.
function downloadReportAsPdf({
  meeting, notes, speakers, decisions, actionItems, topics, takeaways,
  questions, openQuestions, issues, facts, hasFacts, speakerInsights, duration,
}) {
  const doc = new jsPDF({ unit: 'pt', format: 'a4' })
  const pageWidth = doc.internal.pageSize.getWidth()
  const pageHeight = doc.internal.pageSize.getHeight()
  const margin = 48
  const maxWidth = pageWidth - margin * 2
  let y = margin

  function ensureSpace(lineHeight) {
    if (y + lineHeight > pageHeight - margin) {
      doc.addPage()
      y = margin
    }
  }

  function addHeading(text) {
    ensureSpace(24)
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(13)
    doc.setTextColor(20)
    doc.text(text, margin, y)
    y += 18
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(10.5)
  }

  function addParagraph(text) {
    doc.splitTextToSize(text, maxWidth).forEach((line) => {
      ensureSpace(14)
      doc.text(line, margin, y)
      y += 14
    })
    y += 10
  }

  function addList(items) {
    items.forEach((item) => {
      doc.splitTextToSize(`• ${item}`, maxWidth - 10).forEach((line, i) => {
        ensureSpace(14)
        doc.text(line, margin + (i === 0 ? 0 : 10), y)
        y += 14
      })
    })
    y += 10
  }

  doc.setFont('helvetica', 'bold')
  doc.setFontSize(18)
  doc.setTextColor(20)
  doc.text(meeting.title, margin, y)
  y += 22
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(10)
  doc.setTextColor(110)
  doc.text(`${meeting.date} · ${meeting.speakerCount} speakers${duration ? ` · ${duration}` : ''}`, margin, y)
  y += 22
  doc.setTextColor(20)

  addHeading('Meeting Overview')
  addParagraph(notes.purpose || 'Purpose could not be determined from this transcript.')

  addHeading('Executive Summary')
  addParagraph(notes.summary || '')

  addHeading('Key Topics')
  topics.length ? addList(topics) : addParagraph('No clear topics could be identified.')

  addHeading('Key Takeaways')
  takeaways.length ? addList(takeaways) : addParagraph('No standout takeaways were identified.')

  addHeading('Decisions')
  decisions.length
    ? addList(decisions.map((d) => `${d.text}${d.speaker ? ` (${d.speaker})` : ''}`))
    : addParagraph('No explicit decisions identified.')

  addHeading('Action Items')
  actionItems.length
    ? addList(actionItems.map((a) => `${a.owner}, ${a.task} (${a.due ? `due ${a.due}` : 'no deadline specified'})`))
    : addParagraph('No action items were explicitly identified.')

  addHeading('Important Questions')
  questions.length
    ? addList(questions.map((q) => `Q: ${q.question}${q.answer ? `  A: ${q.answer}` : ''}`))
    : addParagraph('No significant questions were identified.')

  addHeading('Issues / Risks')
  issues.length
    ? addList(issues.map((iss) => `${iss.issue}: ${iss.evidence}`))
    : addParagraph('No issues or risks were identified.')

  addHeading('Important Data')
  if (hasFacts) {
    const factLines = []
    if (facts.numbers?.length) factLines.push(`Numbers: ${facts.numbers.join(', ')}`)
    if (facts.dates?.length) factLines.push(`Dates & times: ${facts.dates.join(', ')}`)
    if (facts.technicalTerms?.length) factLines.push(`Technical terms: ${facts.technicalTerms.join(', ')}`)
    addList(factLines)
  } else {
    addParagraph('No notable numbers, dates, or technical terms were identified.')
  }

  addHeading('Speaker Contributions')
  if (speakerInsights.length) {
    addList(speakerInsights.map((s) => `${s.label}: ${s.contributions?.[0] || 'No notable contribution captured.'}`))
  } else {
    addList(speakers.map((s) => s.label))
  }

  addHeading('Open Questions')
  openQuestions.length ? addList(openQuestions) : addParagraph('No unresolved questions remain.')

  addHeading('Conclusion')
  addParagraph(notes.conclusion || 'No conclusion available.')

  const safeName = (meeting.title || 'meeting-report').replace(/[^a-z0-9]+/gi, '-').toLowerCase()
  doc.save(`${safeName}.pdf`)
}

export default function Reports() {
  const { liveMeeting } = useMeeting()
  const isLive = Boolean(liveMeeting)
  const meeting = liveMeeting?.meeting ?? mockMeeting
  const notes = liveMeeting?.notes ?? mockNotes
  const speakers = liveMeeting?.speakers ?? mockSpeakers

  const decisions = notes.decisions ?? []
  const actionItems = notes.actionItems ?? []
  const topics = notes.topics ?? []
  const takeaways = notes.takeaways ?? []
  const questions = notes.questions ?? []
  const openQuestions = notes.openQuestions ?? []
  const issues = notes.issues ?? []
  const facts = notes.facts ?? { numbers: [], dates: [], technicalTerms: [] }
  const speakerInsights = notes.speakerInsights ?? []
  const hasFacts = facts.numbers?.length || facts.dates?.length || facts.technicalTerms?.length
  const duration = formatDuration(meeting.durationSeconds)

  return (
    <div className="page page-reports">
      <p className="eyebrow">{isLive ? 'Your recording · Report' : 'Sample · Report'}</p>
      <h1>{meeting.title}</h1>
      <p className="hero-sub">
        This is the report a meeting produces once notes are generated,
        ready to share with anyone who wasn't in the room.
      </p>

      <div className="report-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() =>
            downloadReportAsPdf({
              meeting, notes, speakers, decisions, actionItems, topics, takeaways,
              questions, openQuestions, issues, facts, hasFacts, speakerInsights, duration,
            })
          }
        >
          Export as PDF
        </button>
      </div>

      <div className="report-sheet">
        <header className="report-sheet-head">
          <h2>{meeting.title}</h2>
          <p>{meeting.date} · {meeting.speakerCount} speakers{duration ? ` · ${duration}` : ''}</p>
        </header>

        <section>
          <h3>Meeting Overview</h3>
          <p>{notes.purpose || 'Purpose could not be determined from this transcript.'}</p>
        </section>

        <section>
          <h3>Executive Summary</h3>
          <p>{notes.summary}</p>
        </section>

        <section>
          <h3>Key Topics</h3>
          {topics.length > 0 ? (
            <ul>{topics.map((t, i) => <li key={i}>{t}</li>)}</ul>
          ) : (
            <p className="notes-empty">No clear topics could be identified.</p>
          )}
        </section>

        <section>
          <h3>Key Takeaways</h3>
          {takeaways.length > 0 ? (
            <ul>{takeaways.map((t, i) => <li key={i}>{t}</li>)}</ul>
          ) : (
            <p className="notes-empty">No standout takeaways were identified.</p>
          )}
        </section>

        <section>
          <h3>Decisions</h3>
          {decisions.length > 0 ? (
            <ul>
              {decisions.map((d, i) => (
                <li key={i}>{d.text}{d.speaker ? ` (${d.speaker})` : ''}</li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No explicit decisions identified.</p>
          )}
        </section>

        <section>
          <h3>Action Items</h3>
          {actionItems.length > 0 ? (
            <ul>
              {actionItems.map((a, i) => (
                <li key={i}>{a.owner}, {a.task} ({a.due ? `due ${a.due}` : 'no deadline specified'})</li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No action items were explicitly identified.</p>
          )}
        </section>

        <section>
          <h3>Important Questions</h3>
          {questions.length > 0 ? (
            <ul>
              {questions.map((q, i) => (
                <li key={i}>
                  <strong>Q:</strong> {q.question}
                  {q.answer && <><br /><strong>A:</strong> {q.answer}</>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No significant questions were identified.</p>
          )}
        </section>

        <section>
          <h3>Issues / Risks</h3>
          {issues.length > 0 ? (
            <ul>
              {issues.map((iss, i) => (
                <li key={i}><strong>{iss.issue}:</strong> {iss.evidence}</li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No issues or risks were identified.</p>
          )}
        </section>

        <section>
          <h3>Important Data</h3>
          {hasFacts ? (
            <ul>
              {facts.numbers?.length > 0 && <li><strong>Numbers:</strong> {facts.numbers.join(', ')}</li>}
              {facts.dates?.length > 0 && <li><strong>Dates &amp; times:</strong> {facts.dates.join(', ')}</li>}
              {facts.technicalTerms?.length > 0 && <li><strong>Technical terms:</strong> {facts.technicalTerms.join(', ')}</li>}
            </ul>
          ) : (
            <p className="notes-empty">No notable numbers, dates, or technical terms were identified.</p>
          )}
        </section>

        <section>
          <h3>Speaker Contributions</h3>
          {speakerInsights.length > 0 ? (
            <ul>
              {speakerInsights.map((s) => (
                <li key={s.id}>
                  <strong>{s.label}:</strong> {s.contributions?.[0] || 'No notable contribution captured.'}
                </li>
              ))}
            </ul>
          ) : (
            <ul>{speakers.map((s) => <li key={s.id}>{s.label}</li>)}</ul>
          )}
        </section>

        <section>
          <h3>Open Questions</h3>
          {openQuestions.length > 0 ? (
            <ul>{openQuestions.map((q, i) => <li key={i}>{q}</li>)}</ul>
          ) : (
            <p className="notes-empty">No unresolved questions remain.</p>
          )}
        </section>

        <section>
          <h3>Conclusion</h3>
          <p>{notes.conclusion || 'No conclusion available.'}</p>
        </section>
      </div>
    </div>
  )
}
