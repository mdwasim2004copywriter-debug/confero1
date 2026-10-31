import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, authFetch } from '../context/AuthContext.jsx'
import { useMeeting } from '../context/MeetingContext.jsx'
import { API_BASE } from '../config/api.js'
import { CategoryBarChart, DonutChart, ScatterPlot, TrendLine } from '../components/charts.jsx'
import { formatBytes } from '../components/MetricsPanel.jsx'

const CATEGORY_LABELS = { mic: 'Live · Mic', tab: 'Live · Tab capture', upload: 'Upload' }
const CATEGORY_COLORS = { mic: 'var(--teal)', tab: 'var(--periwinkle)', upload: 'var(--coral)' }
const MEDIA_COLORS = { audio: 'var(--moss)', video: 'var(--amber)' }

function formatMinutes(totalSeconds) {
  const mins = Math.round(totalSeconds / 60)
  return mins < 1 ? '<1 min' : `${mins} min`
}

// Guests have no backend row to query -- guestHistory already holds each
// of their full results (including .metrics) in localStorage, so this
// builds the exact same row shape GET /api/meetings/metrics returns for
// a logged-in user, instead of needing a second data path downstream.
function metricsRowsFromGuestHistory(guestHistory) {
  return guestHistory
    .filter((g) => g.metrics)
    .map((g) => ({
      id: g.meeting.id,
      title: g.meeting.title,
      date: g.meeting.date,
      durationSeconds: g.meeting.durationSeconds,
      sourceCategory: g.metrics.source,
      mediaType: g.metrics.mediaType,
      fileSizeBytes: g.metrics.fileSizeBytes,
      totalSeconds: g.metrics.timingSeconds?.total,
    }))
    .reverse() // guestHistory is newest-first; metrics rows are oldest-first, same as the backend's list
}

export default function Dashboard() {
  const { isLoggedIn, token } = useAuth()
  const { guestHistory, loadMeetingById, setLiveMeeting } = useMeeting()
  const navigate = useNavigate()
  const [rows, setRows] = useState(null) // null = loading (logged-in only)
  const [loadError, setLoadError] = useState(null)
  // Clicking a category bar/donut slice filters "Recent activity" to just
  // that category -- click the same one again (or the "Clear" chip) to
  // reset. Shared across both charts so picking either one filters the
  // same list and highlights the same selection in both places.
  const [selectedCategory, setSelectedCategory] = useState(null)

  useEffect(() => {
    if (!isLoggedIn) {
      setRows(metricsRowsFromGuestHistory(guestHistory))
      return
    }
    let cancelled = false
    authFetch(`${API_BASE}/meetings/metrics`, token)
      .then((res) => (res.ok ? res.json() : Promise.reject()))
      .then((data) => {
        if (!cancelled) setRows(data)
      })
      .catch(() => {
        if (!cancelled) setLoadError('Could not load your processing metrics, is the backend running?')
      })
    return () => {
      cancelled = true
    }
  }, [isLoggedIn, token, guestHistory])

  async function openMeeting(id) {
    if (!isLoggedIn) {
      const guestMeeting = guestHistory.find((g) => g.meeting.id === id)
      if (guestMeeting) {
        setLiveMeeting(guestMeeting)
        navigate('/transcript')
      }
      return
    }
    try {
      await loadMeetingById(id)
      navigate('/transcript')
    } catch (err) {
      console.error(err)
    }
  }

  // Only recordings that actually have a category/processing-time (i.e.
  // made after this feature existed) feed the charts -- an older
  // recording still counts toward the simple totals below, but isn't
  // shown as a fake "uncategorized" bar.
  const categorized = useMemo(() => (rows || []).filter((r) => r.sourceCategory && r.totalSeconds != null), [rows])
  const uncategorizedCount = (rows || []).length - categorized.length

  const totals = useMemo(() => {
    const totalRecordings = (rows || []).length
    const totalAudioSeconds = (rows || []).reduce((sum, r) => sum + (r.durationSeconds || 0), 0)
    const totalProcessingSeconds = categorized.reduce((sum, r) => sum + r.totalSeconds, 0)
    const avgProcessingSeconds = categorized.length ? totalProcessingSeconds / categorized.length : 0
    return { totalRecordings, totalAudioSeconds, totalProcessingSeconds, avgProcessingSeconds }
  }, [rows, categorized])

  const byCategory = useMemo(() => {
    const groups = {}
    for (const r of categorized) {
      groups[r.sourceCategory] ??= { audio: 0, video: 0, totalSeconds: 0, count: 0 }
      const mt = r.mediaType === 'video' ? 'video' : 'audio'
      groups[r.sourceCategory][mt] += 1
      groups[r.sourceCategory].totalSeconds += r.totalSeconds
      groups[r.sourceCategory].count += 1
    }
    return groups
  }, [categorized])

  const countChartGroups = useMemo(
    () =>
      Object.entries(byCategory).map(([key, g]) => ({
        label: CATEGORY_LABELS[key] || key,
        segments: [
          { value: g.audio, color: MEDIA_COLORS.audio, title: `${CATEGORY_LABELS[key] || key} · Audio: ${g.audio}` },
          { value: g.video, color: MEDIA_COLORS.video, title: `${CATEGORY_LABELS[key] || key} · Video: ${g.video}` },
        ],
      })),
    [byCategory]
  )

  const avgTimeChartGroups = useMemo(
    () =>
      Object.entries(byCategory).map(([key, g]) => {
        const avg = g.count ? g.totalSeconds / g.count : 0
        return {
          label: CATEGORY_LABELS[key] || key,
          segments: [{ value: avg, color: CATEGORY_COLORS[key] || 'var(--teal)', title: `${CATEGORY_LABELS[key] || key}: ${avg.toFixed(1)}s avg` }],
        }
      }),
    [byCategory]
  )

  const donutSlices = useMemo(
    () =>
      Object.entries(byCategory).map(([key, g]) => ({
        label: CATEGORY_LABELS[key] || key,
        value: g.count,
        color: CATEGORY_COLORS[key] || 'var(--teal)',
      })),
    [byCategory]
  )

  const scatterPoints = useMemo(
    () =>
      categorized.map((r) => ({
        id: r.id,
        x: r.durationSeconds || 0,
        y: r.totalSeconds,
        color: CATEGORY_COLORS[r.sourceCategory] || 'var(--teal)',
        label: `${r.title} · ${Math.round(r.durationSeconds || 0)}s audio, ${r.totalSeconds.toFixed(1)}s to process (click to open)`,
      })),
    [categorized]
  )

  const trendPoints = useMemo(
    () =>
      categorized.map((r, i) => ({
        id: r.id,
        y: r.totalSeconds,
        color: CATEGORY_COLORS[r.sourceCategory] || 'var(--teal)',
        label: `#${i + 1} · ${r.title} · ${r.totalSeconds.toFixed(1)}s (click to open)`,
      })),
    [categorized]
  )

  const visibleRows = useMemo(() => {
    const ordered = [...(rows || [])].reverse()
    const filtered = selectedCategory ? ordered.filter((r) => r.sourceCategory === selectedCategory) : ordered
    return filtered.slice(0, 10)
  }, [rows, selectedCategory])

  const stillLoading = isLoggedIn && rows === null && !loadError

  function toggleCategory(label) {
    const key = Object.keys(CATEGORY_LABELS).find((k) => CATEGORY_LABELS[k] === label) || label
    setSelectedCategory((prev) => (prev === key ? null : key))
  }

  return (
    <div className="page page-dashboard">
      <p className="eyebrow">Performance</p>
      <h1>Dashboard</h1>
      <p className="hero-sub">
        How Confero's pipeline performs across every recording you've made, by capture method, media type, and
        processing time. Click a bar, slice, or point to dig in.
      </p>

      {!isLoggedIn && <p className="guest-note">Showing metrics for recordings saved on this browser only.</p>}
      {isLoggedIn && loadError && <p className="status-banner status-error">{loadError}</p>}
      {stillLoading && <p className="hero-sub">Loading…</p>}

      {!stillLoading && (rows || []).length === 0 && (
        <p className="hero-sub">No recordings yet, head to Record Meeting to make your first one.</p>
      )}

      {!stillLoading && (rows || []).length > 0 && (
        <>
          <div className="dashboard-tiles">
            <div className="dashboard-tile">
              <p className="dashboard-tile-value">{totals.totalRecordings}</p>
              <p className="dashboard-tile-label">Recordings</p>
            </div>
            <div className="dashboard-tile">
              <p className="dashboard-tile-value">{formatMinutes(totals.totalAudioSeconds)}</p>
              <p className="dashboard-tile-label">Audio processed</p>
            </div>
            <div className="dashboard-tile">
              <p className="dashboard-tile-value">{formatMinutes(totals.totalProcessingSeconds)}</p>
              <p className="dashboard-tile-label">Total processing time</p>
            </div>
            <div className="dashboard-tile">
              <p className="dashboard-tile-value">{totals.avgProcessingSeconds.toFixed(1)}s</p>
              <p className="dashboard-tile-label">Avg. per recording</p>
            </div>
          </div>

          {categorized.length === 0 ? (
            <p className="hero-sub">
              Nothing to chart yet -- these {totals.totalRecordings} recording{totals.totalRecordings === 1 ? '' : 's'}{' '}
              {totals.totalRecordings === 1 ? 'was' : 'were'} made before this dashboard existed, so there's no
              category/timing breakdown for them. Record something new (mic, tab capture, or an upload) to see it
              show up here.
            </p>
          ) : (
            <>
              {selectedCategory && (
                <button type="button" className="btn btn-ghost chart-filter-chip" onClick={() => setSelectedCategory(null)}>
                  Filtering: {CATEGORY_LABELS[selectedCategory] || selectedCategory} ✕
                </button>
              )}

              <div className="dashboard-grid">
                <div className="dashboard-section">
                  <h2>Share of recordings by category</h2>
                  <DonutChart slices={donutSlices} selected={selectedCategory && (CATEGORY_LABELS[selectedCategory] || selectedCategory)} onSelect={toggleCategory} />
                </div>

                <div className="dashboard-section">
                  <h2>Recordings by category</h2>
                  <CategoryBarChart
                    groups={countChartGroups}
                    selected={selectedCategory && (CATEGORY_LABELS[selectedCategory] || selectedCategory)}
                    onSelect={toggleCategory}
                  />
                  <div className="chart-legend">
                    <span className="timing-legend-item">
                      <span className="timing-legend-dot" style={{ background: MEDIA_COLORS.audio }} />
                      Audio
                    </span>
                    <span className="timing-legend-item">
                      <span className="timing-legend-dot" style={{ background: MEDIA_COLORS.video }} />
                      Video
                    </span>
                  </div>
                </div>
              </div>

              <div className="dashboard-section">
                <h2>Average processing time by category</h2>
                <CategoryBarChart
                  groups={avgTimeChartGroups}
                  selected={selectedCategory && (CATEGORY_LABELS[selectedCategory] || selectedCategory)}
                  onSelect={toggleCategory}
                />
              </div>

              {scatterPoints.length > 0 && (
                <div className="dashboard-section">
                  <h2>Processing time vs. audio duration</h2>
                  <ScatterPlot points={scatterPoints} xLabel="Audio duration (s)" yLabel="Processing time (s)" onSelect={openMeeting} />
                </div>
              )}

              {trendPoints.length > 1 && (
                <div className="dashboard-section">
                  <h2>Processing time across your recordings</h2>
                  <TrendLine points={trendPoints} xLabel="Recording # (chronological)" yLabel="Processing time (s)" onSelect={openMeeting} />
                </div>
              )}
            </>
          )}

          <div className="dashboard-section">
            <h2>Recent activity</h2>
            {uncategorizedCount > 0 && (
              <p className="dashboard-note">
                {uncategorizedCount} older recording{uncategorizedCount === 1 ? '' : 's'} not shown in the charts above.
              </p>
            )}
            <div className="history-list">
              {visibleRows.map((r) => (
                <button type="button" className="history-row history-row-clickable" key={r.id} onClick={() => openMeeting(r.id)}>
                  <div className="history-row-main">
                    <p className="meeting-card-title">{r.title}</p>
                    <p className="meeting-card-meta">
                      {r.date} · {CATEGORY_LABELS[r.sourceCategory] || 'Uncategorized'} · {r.mediaType || '—'} ·{' '}
                      {formatBytes(r.fileSizeBytes)} · {r.totalSeconds != null ? `${r.totalSeconds.toFixed(1)}s to process` : '—'}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
