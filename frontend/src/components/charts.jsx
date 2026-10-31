import { useCallback, useEffect, useRef, useState } from 'react'

// Small, dependency-free chart primitives built from the site's own design
// tokens (colors passed in as CSS var() strings, styling from styles.css)
// instead of a charting library -- keeps the project dependency-free for
// whoever else has to `npm install` it. Interactivity (hover tooltip,
// hover highlight, click-to-select, a grow-in entrance) is plain React
// state + CSS transitions, not a library either.

// Shared by every chart below: tracks a floating tooltip's position
// (relative to the chart's own wrapper, so it works no matter where the
// chart sits on the page) and content.
function useChartTooltip() {
  const wrapRef = useRef(null)
  const [tooltip, setTooltip] = useState(null) // { x, y, content } | null

  const showTooltip = useCallback((e, content) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, content })
  }, [])
  const hideTooltip = useCallback(() => setTooltip(null), [])

  return { wrapRef, tooltip, showTooltip, hideTooltip }
}

function ChartTooltip({ tooltip }) {
  if (!tooltip) return null
  return (
    <div className="chart-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
      {tooltip.content}
    </div>
  )
}

// Flips true one tick after mount -- every chart renders its "zero" state
// first, then this flip lets CSS transitions animate up to the real
// values instead of popping straight in.
function useGrowIn() {
  const [grown, setGrown] = useState(false)
  useEffect(() => {
    const id = requestAnimationFrame(() => setGrown(true))
    return () => cancelAnimationFrame(id)
  }, [])
  return grown
}

export function CategoryBarChart({ groups, height = 160, selected, onSelect }) {
  const { wrapRef, tooltip, showTooltip, hideTooltip } = useChartTooltip()
  const grown = useGrowIn()
  const maxTotal = Math.max(1, ...groups.map((g) => g.segments.reduce((sum, seg) => sum + seg.value, 0)))
  const barWidth = 56
  const gap = 36
  const width = groups.length * (barWidth + gap) + gap

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg viewBox={`0 0 ${width} ${height + 28}`} className="chart-svg" role="img" style={{ width: '100%', height: 'auto' }}>
        <line x1={gap / 2} y1={height} x2={width - gap / 2} y2={height} className="chart-axis-line" />
        {groups.map((g, i) => {
          const x = gap + i * (barWidth + gap) - gap / 2
          const total = g.segments.reduce((sum, seg) => sum + seg.value, 0)
          const isSelected = selected === g.label
          const isDimmed = selected != null && !isSelected
          let yCursor = height
          return (
            <g
              key={g.label}
              className={`chart-bar-group${isDimmed ? ' is-dimmed' : ''}`}
              style={{ cursor: onSelect ? 'pointer' : 'default' }}
              onClick={() => onSelect?.(isSelected ? null : g.label)}
            >
              {g.segments.map((seg, j) => {
                const segHeight = grown ? (seg.value / maxTotal) * (height - 12) : 0
                yCursor -= segHeight
                if (seg.value <= 0) return null
                return (
                  <rect
                    key={j}
                    x={x}
                    y={yCursor}
                    width={barWidth}
                    height={Math.max(segHeight, 1)}
                    fill={seg.color}
                    rx="3"
                    className="chart-bar-rect"
                    style={{ transitionDelay: `${i * 70 + j * 40}ms` }}
                    onMouseMove={(e) => showTooltip(e, seg.title)}
                    onMouseLeave={hideTooltip}
                  />
                )
              })}
              <text x={x + barWidth / 2} y={height + 18} textAnchor="middle" className="chart-axis-label">
                {g.label}
              </text>
              {total > 0 && (
                <text
                  x={x + barWidth / 2}
                  y={height - (grown ? (total / maxTotal) * (height - 12) : 0) - 6}
                  textAnchor="middle"
                  className="chart-value-label"
                  style={{ transitionDelay: `${i * 70}ms` }}
                >
                  {Number.isInteger(total) ? total : total.toFixed(1)}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <ChartTooltip tooltip={tooltip} />
    </div>
  )
}

export function DonutChart({ slices, size = 180, selected, onSelect }) {
  const { wrapRef, tooltip, showTooltip, hideTooltip } = useChartTooltip()
  const grown = useGrowIn()
  const total = Math.max(1, slices.reduce((sum, s) => sum + s.value, 0))
  const radius = size / 2 - 16
  const circumference = 2 * Math.PI * radius
  const strokeWidth = 24
  let offsetAcc = 0

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg viewBox={`0 0 ${size} ${size}`} className="chart-svg" role="img" style={{ width: size, height: size }}>
        <g transform={`translate(${size / 2}, ${size / 2}) rotate(-90)`}>
          <circle r={radius} fill="none" stroke="var(--hairline)" strokeWidth={strokeWidth} />
          {slices.map((s, i) => {
            if (s.value <= 0) return null
            const fraction = s.value / total
            const dash = grown ? fraction * circumference : 0
            const rotation = (offsetAcc / total) * 360
            offsetAcc += s.value
            const isSelected = selected === s.label
            const isDimmed = selected != null && !isSelected
            return (
              <circle
                key={s.label}
                r={radius}
                fill="none"
                stroke={s.color}
                strokeWidth={isSelected ? strokeWidth + 4 : strokeWidth}
                strokeDasharray={`${dash} ${circumference - dash}`}
                transform={`rotate(${rotation})`}
                className={`chart-donut-slice${isDimmed ? ' is-dimmed' : ''}`}
                style={{ cursor: onSelect ? 'pointer' : 'default', transitionDelay: `${i * 90}ms` }}
                onMouseMove={(e) => showTooltip(e, `${s.label}: ${s.value} (${Math.round(fraction * 100)}%)`)}
                onMouseLeave={hideTooltip}
                onClick={() => onSelect?.(isSelected ? null : s.label)}
              />
            )
          })}
        </g>
        <text x={size / 2} y={size / 2 - 2} textAnchor="middle" className="chart-donut-total">
          {total}
        </text>
        <text x={size / 2} y={size / 2 + 16} textAnchor="middle" className="chart-axis-label">
          recordings
        </text>
      </svg>
      <ChartTooltip tooltip={tooltip} />
    </div>
  )
}

export function ScatterPlot({ points, width = 480, height = 220, xLabel, yLabel, onSelect }) {
  const { wrapRef, tooltip, showTooltip, hideTooltip } = useChartTooltip()
  const grown = useGrowIn()
  const pad = 40
  const xMax = Math.max(1, ...points.map((p) => p.x)) * 1.1
  const yMax = Math.max(1, ...points.map((p) => p.y)) * 1.1
  const sx = (x) => pad + (x / xMax) * (width - pad - 16)
  const sy = (y) => height - pad - (y / yMax) * (height - pad - 16)

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg" role="img" style={{ width: '100%', height: 'auto' }}>
        <line x1={pad} y1={height - pad} x2={width - 8} y2={height - pad} className="chart-axis-line" />
        <line x1={pad} y1={height - pad} x2={pad} y2={8} className="chart-axis-line" />
        <text x={(width + pad) / 2} y={height - 6} textAnchor="middle" className="chart-axis-label">
          {xLabel}
        </text>
        <text x={8} y={16} className="chart-axis-label">
          {yLabel}
        </text>
        {points.map((p, i) => (
          <circle
            key={p.id ?? i}
            cx={sx(p.x)}
            cy={sy(p.y)}
            r={grown ? 5 : 0}
            fill={p.color}
            className="chart-dot"
            style={{ cursor: onSelect ? 'pointer' : 'default', transitionDelay: `${i * 25}ms` }}
            onMouseMove={(e) => showTooltip(e, p.label)}
            onMouseLeave={hideTooltip}
            onClick={() => onSelect?.(p.id)}
          />
        ))}
      </svg>
      <ChartTooltip tooltip={tooltip} />
    </div>
  )
}

// Connects points in the given order (chronological, by recording) with a
// line -- shows the trend, not just the distribution. x is each point's
// position in the sequence, not a real timestamp axis -- recordings
// aren't evenly spaced in time, and the trend across "the Nth recording"
// is what's actually meaningful to show here.
export function TrendLine({ points, width = 480, height = 220, xLabel, yLabel, onSelect }) {
  const { wrapRef, tooltip, showTooltip, hideTooltip } = useChartTooltip()
  const grown = useGrowIn()
  const pad = 40
  const xMax = Math.max(1, points.length - 1)
  const yMax = Math.max(1, ...points.map((p) => p.y)) * 1.15
  const sx = (i) => pad + (i / xMax) * (width - pad - 16)
  const sy = (y) => height - pad - (grown ? (y / yMax) * (height - pad - 16) : 0)

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${sx(i)} ${sy(p.y)}`).join(' ')

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg" role="img" style={{ width: '100%', height: 'auto' }}>
        <line x1={pad} y1={height - pad} x2={width - 8} y2={height - pad} className="chart-axis-line" />
        <line x1={pad} y1={height - pad} x2={pad} y2={8} className="chart-axis-line" />
        <text x={(width + pad) / 2} y={height - 6} textAnchor="middle" className="chart-axis-label">
          {xLabel}
        </text>
        <text x={8} y={16} className="chart-axis-label">
          {yLabel}
        </text>
        <path d={pathD} className="chart-trend-line" fill="none" />
        {points.map((p, i) => (
          <circle
            key={p.id ?? i}
            cx={sx(i)}
            cy={sy(p.y)}
            r="5"
            fill={p.color || 'var(--teal)'}
            className="chart-dot"
            style={{ cursor: onSelect ? 'pointer' : 'default' }}
            onMouseMove={(e) => showTooltip(e, p.label)}
            onMouseLeave={hideTooltip}
            onClick={() => onSelect?.(p.id)}
          />
        ))}
      </svg>
      <ChartTooltip tooltip={tooltip} />
    </div>
  )
}
