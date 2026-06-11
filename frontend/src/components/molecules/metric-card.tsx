/**
 * Metric Card Component
 *
 * Displays a KPI metric with trend indicator and optional sparkline.
 * Uses monospace numerals for data-dense display.
 */

import { ArrowUp, ArrowDown } from 'lucide-react'
import { AreaChart, Area, ResponsiveContainer } from 'recharts'
import { Card, CardContent } from '@/components/atoms/card'
import { cn } from '@/lib/utils'

interface MetricCardProps {
  title: string
  value: string | number
  change?: number
  changeLabel?: string
  icon?: React.ReactNode
  sparklineData?: number[]
  sparklineColor?: string
  className?: string
}

export function MetricCard({
  title,
  value,
  change,
  changeLabel,
  icon,
  sparklineData,
  sparklineColor,
  className,
}: MetricCardProps) {
  const isPositive = change !== undefined && change > 0
  const isNegative = change !== undefined && change < 0

  // Determine sparkline color from trend direction
  const chartColor = sparklineColor || (isPositive ? '#34d399' : isNegative ? '#f87171' : '#E64E20')

  // Convert raw numbers to recharts data format
  const chartData = sparklineData?.map((v) => ({ v }))

  return (
    <Card className={cn('relative overflow-hidden', className)}>
      <CardContent className="p-5">
        {/* Header row: label + icon */}
        <div className="flex items-center justify-between">
          <span className="text-[13px] font-medium text-muted-foreground">
            {title}
          </span>
          {icon && (
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted text-muted-foreground">
              {icon}
            </div>
          )}
        </div>

        {/* Big number — monospace for data feel */}
        <div className="mt-3 font-mono text-3xl font-bold tracking-tight">
          {value}
        </div>

        {/* Trend indicator */}
        {change !== undefined && (
          <div className="mt-2 flex items-center gap-1.5 text-xs">
            {isPositive && (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-400">
                <ArrowUp className="h-3 w-3" />
                +{change}%
              </span>
            )}
            {isNegative && (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-red-500/10 px-1.5 py-0.5 font-medium text-red-400">
                <ArrowDown className="h-3 w-3" />
                {change}%
              </span>
            )}
            {changeLabel && (
              <span className="text-muted-foreground">{changeLabel}</span>
            )}
          </div>
        )}
      </CardContent>

      {/* Sparkline — subtle area chart at bottom of card */}
      {chartData && chartData.length > 1 && (
        <div className="absolute bottom-0 left-0 right-0 h-12 opacity-40">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`spark-${title.replace(/\s/g, '')}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={chartColor} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={chartColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="v"
                stroke={chartColor}
                strokeWidth={1.5}
                fill={`url(#spark-${title.replace(/\s/g, '')})`}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  )
}
