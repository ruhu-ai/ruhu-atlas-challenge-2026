/**
 * Usage Widget Component
 *
 * Displays usage metrics with progress bars and limits.
 */

import { Card } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { AlertCircle } from 'lucide-react'
import type { UsageMetrics } from '@/types/billing'

interface UsageWidgetProps {
  usage: UsageMetrics
  className?: string
}

interface UsageItemProps {
  label: string
  used: number
  limit: number | null
  percentage: number
  unit?: string
}

function UsageItem({ label, used, limit, percentage, unit = '' }: UsageItemProps) {
  const isUnlimited = limit === null
  const isNearLimit = percentage >= 80 && !isUnlimited
  const isAtLimit = percentage >= 100 && !isUnlimited

  const getProgressColor = () => {
    if (isAtLimit) return 'bg-red-500'
    if (isNearLimit) return 'bg-amber-500'
    return 'bg-blue-500'
  }

  const getTextColor = () => {
    if (isAtLimit) return 'text-red-600 dark:text-red-400'
    if (isNearLimit) return 'text-amber-600 dark:text-amber-400'
    return 'text-gray-900 dark:text-gray-100'
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
        <div className="flex items-center gap-2">
          {isNearLimit && !isAtLimit && (
            <AlertCircle className="w-4 h-4 text-amber-500" />
          )}
          {isAtLimit && <AlertCircle className="w-4 h-4 text-red-500" />}
          <span className={`text-sm font-semibold ${getTextColor()}`}>
            {used.toLocaleString()}
            {!isUnlimited && (
              <>
                {' '}
                / {limit.toLocaleString()}
                {unit}
              </>
            )}
            {isUnlimited && ` ${unit}`}
          </span>
        </div>
      </div>

      {/* Progress Bar */}
      {!isUnlimited && (
        <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2 overflow-hidden">
          <div
            className={`h-full ${getProgressColor()} transition-all duration-300`}
            style={{ width: `${Math.min(percentage, 100)}%` }}
          />
        </div>
      )}

      {/* Unlimited Badge */}
      {isUnlimited && (
        <div className="flex justify-end">
          <Badge variant="outline" className="text-xs">
            Unlimited
          </Badge>
        </div>
      )}

      {/* Warning Messages */}
      {isAtLimit && (
        <p className="text-xs text-red-600 dark:text-red-400">
          Limit reached. Upgrade to continue.
        </p>
      )}
      {isNearLimit && !isAtLimit && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Approaching limit. Consider upgrading.
        </p>
      )}
    </div>
  )
}

export function UsageWidget({ usage, className = '' }: UsageWidgetProps) {
  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  }

  const limits = usage.limits ?? {}
  const pct = usage.usage_percentage ?? {}

  return (
    <Card className={`p-6 ${className}`}>
      <div className="mb-6">
        <h3 className="text-lg font-semibold mb-2">Usage This Period</h3>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          {formatDate(usage.period_start)} - {formatDate(usage.period_end)}
        </p>
      </div>

      <div className="space-y-6">
        <UsageItem
          label="AI Agents"
          used={usage.agents_created ?? 0}
          limit={limits.max_agents ?? null}
          percentage={pct.agents ?? 0}
        />

        <UsageItem
          label="Conversations"
          used={usage.conversations_count ?? 0}
          limit={limits.max_conversations_monthly ?? null}
          percentage={pct.conversations ?? 0}
        />

        <UsageItem
          label="Voice Minutes"
          used={usage.voice_minutes_used ?? 0}
          limit={limits.max_voice_minutes_monthly ?? null}
          percentage={pct.voice_minutes ?? 0}
          unit="min"
        />

        <UsageItem
          label="Team Members"
          used={usage.team_members_count ?? 0}
          limit={limits.max_team_members ?? null}
          percentage={pct.team_members ?? 0}
        />
      </div>
    </Card>
  )
}
