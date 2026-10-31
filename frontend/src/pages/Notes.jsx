import { Link } from 'react-router-dom'
import { useMeeting } from '../context/MeetingContext.jsx'
import { meeting as mockMeeting, notes as mockNotes } from '../data/mockMeeting'

export default function Notes() {
  const { liveMeeting } = useMeeting()
  const isLive = Boolean(liveMeeting)
  const meeting = liveMeeting?.meeting ?? mockMeeting
  const notes = liveMeeting?.notes ?? mockNotes

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

  return (
    <div className="page page-notes">
      <div className="page-head-row">
        <div>
          <p className="eyebrow">{isLive ? 'Your recording · Meeting notes' : 'Sample output · Meeting notes'}</p>
          <h1>{meeting.title}</h1>
        </div>
        <Link to="/reports" className="btn btn-primary">Build report →</Link>
      </div>

      <section className="notes-card">
        <h2 className="notes-card-title">Executive Summary</h2>
        <p className="notes-summary">{notes.summary}</p>
        {notes.purpose && <p className="notes-purpose"><strong>Purpose:</strong> {notes.purpose}</p>}
      </section>

      <section className="notes-card">
        <h2 className="notes-card-title">Key Topics</h2>
        {topics.length > 0 ? (
          <div className="topic-chips">
            {topics.map((t, i) => (
              <span key={i} className="topic-chip">{t}</span>
            ))}
          </div>
        ) : (
          <p className="notes-empty">No clear topics could be identified from this transcript.</p>
        )}
      </section>

      <section className="notes-card">
        <h2 className="notes-card-title">Key Takeaways</h2>
        {takeaways.length > 0 ? (
          <ul className="notes-list">
            {takeaways.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        ) : (
          <p className="notes-empty">No standout takeaways were identified in this conversation.</p>
        )}
      </section>

      <div className="notes-grid">
        <section className="notes-card">
          <h2 className="notes-card-title">Decisions</h2>
          {decisions.length > 0 ? (
            <ul className="notes-list decision-list">
              {decisions.map((d, i) => (
                <li key={i}>
                  <span className="decision-text">{d.text}</span>
                  {d.speaker && <span className="decision-speaker">, {d.speaker}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No explicit decisions identified.</p>
          )}
        </section>

        <section className="notes-card">
          <h2 className="notes-card-title">Action Items</h2>
          {actionItems.length > 0 ? (
            <ul className="action-list">
              {actionItems.map((a, i) => (
                <li key={i}>
                  <span className="action-owner">{a.owner}</span>
                  <span className="action-task">{a.task}</span>
                  <span className="action-due">{a.due ? `Due ${a.due}` : 'No deadline specified'}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No action items were explicitly identified in this conversation.</p>
          )}
        </section>
      </div>

      <div className="notes-grid">
        <section className="notes-card">
          <h2 className="notes-card-title">Important Questions</h2>
          {questions.length > 0 ? (
            <ul className="qa-list">
              {questions.map((q, i) => (
                <li key={i}>
                  <p className="qa-question">{q.question}</p>
                  {q.answer && <p className="qa-answer">{q.answer}</p>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No significant questions were identified.</p>
          )}
        </section>

        <section className="notes-card">
          <h2 className="notes-card-title">Issues / Risks</h2>
          {issues.length > 0 ? (
            <ul className="notes-list">
              {issues.map((iss, i) => (
                <li key={i}>
                  <span className="issue-label">{iss.issue}</span>
                  <span className="issue-evidence">{iss.evidence}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="notes-empty">No issues or risks were identified in this conversation.</p>
          )}
        </section>
      </div>

      {openQuestions.length > 0 && (
        <section className="notes-card">
          <h2 className="notes-card-title">Open Questions</h2>
          <ul className="notes-list">
            {openQuestions.map((q, i) => <li key={i}>{q}</li>)}
          </ul>
        </section>
      )}

      <section className="notes-card">
        <h2 className="notes-card-title">Important Data</h2>
        {hasFacts ? (
          <div className="facts-grid">
            {facts.numbers?.length > 0 && (
              <div>
                <h3 className="facts-subtitle">Numbers</h3>
                <div className="topic-chips">{facts.numbers.map((n, i) => <span key={i} className="topic-chip">{n}</span>)}</div>
              </div>
            )}
            {facts.dates?.length > 0 && (
              <div>
                <h3 className="facts-subtitle">Dates &amp; Times</h3>
                <div className="topic-chips">{facts.dates.map((d, i) => <span key={i} className="topic-chip">{d}</span>)}</div>
              </div>
            )}
            {facts.technicalTerms?.length > 0 && (
              <div>
                <h3 className="facts-subtitle">Technical Terms</h3>
                <div className="topic-chips">{facts.technicalTerms.map((t, i) => <span key={i} className="topic-chip">{t}</span>)}</div>
              </div>
            )}
          </div>
        ) : (
          <p className="notes-empty">No notable numbers, dates, or technical terms were identified.</p>
        )}
      </section>

      <section className="notes-card">
        <h2 className="notes-card-title">Speaker Contributions</h2>
        {speakerInsights.length > 0 ? (
          <div className="speaker-insight-grid">
            {speakerInsights.map((s) => (
              <div className="speaker-insight-card" key={s.id}>
                <h3 className="speaker-insight-name">{s.label}</h3>
                {s.contributions?.length > 0 && (
                  <ul className="notes-list">
                    {s.contributions.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                )}
                {s.questionsRaised?.length > 0 && (
                  <p className="speaker-insight-meta">Asked: {s.questionsRaised.length} notable question{s.questionsRaised.length === 1 ? '' : 's'}</p>
                )}
                {s.decisionsOrActions?.length > 0 && (
                  <p className="speaker-insight-meta">Involved in {s.decisionsOrActions.length} decision(s)/action(s)</p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="notes-empty">No per-speaker breakdown available.</p>
        )}
      </section>

      {notes.conclusion && (
        <section className="notes-card">
          <h2 className="notes-card-title">Conclusion</h2>
          <p className="notes-summary">{notes.conclusion}</p>
        </section>
      )}
    </div>
  )
}
