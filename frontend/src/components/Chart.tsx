import { useEffect, useRef } from 'react'
import type { EChartsOption } from 'echarts'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

export function Chart({ option, className = '' }: { option: EChartsOption; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    chart.setOption(option)
    const resize = new ResizeObserver(() => chart.resize())
    resize.observe(ref.current)
    return () => { resize.disconnect(); chart.dispose() }
  }, [option])
  return <div ref={ref} className={`chart ${className}`} role="img" aria-label="Network traffic chart" />
}

export const chartText = '#8a96a6'
export const chartGrid = 'rgba(207,224,242,.07)'
