import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Loader2, RefreshCw, Rocket, AlertTriangle, CheckCircle2 } from 'lucide-react'
import type { AgentPublishReadiness, AgentVersionSummary } from '@/types/agent-definition'

interface AgentPublishViewProps {
  versions: AgentVersionSummary[]
  review?: AgentPublishReadiness | null
  loadingVersions: boolean
  loadingReview: boolean
  publishing: boolean
  onRefresh: () => void
  onPublish: () => void
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'n/a'
  return date.toLocaleString()
}

export function AgentPublishView({
  versions,
  review,
  loadingVersions,
  loadingReview,
  publishing,
  onRefresh,
  onPublish,
}: AgentPublishViewProps) {
  const currentDraft = versions.find((version) => version.is_current_draft) || null
  const currentPublished = versions.find((version) => version.is_current_published) || null

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Publish Agent</CardTitle>
          <CardDescription>
            Review draft readiness, validation blockers, and publish the current agent definition.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-md border border-border p-4">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Current draft</p>
              {loadingVersions ? (
                <p className="mt-2 text-sm text-muted-foreground">Loading...</p>
              ) : currentDraft ? (
                <>
                  <p className="mt-2 text-lg font-semibold">v{currentDraft.version_number}</p>
                  <p className="text-xs text-muted-foreground">{formatDateTime(currentDraft.updated_at)}</p>
                </>
              ) : (
                <p className="mt-2 text-sm text-muted-foreground">No draft exists yet.</p>
              )}
            </div>
            <div className="rounded-md border border-border p-4">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Live version</p>
              {loadingVersions ? (
                <p className="mt-2 text-sm text-muted-foreground">Loading...</p>
              ) : currentPublished ? (
                <>
                  <p className="mt-2 text-lg font-semibold">v{currentPublished.version_number}</p>
                  <p className="text-xs text-muted-foreground">{formatDateTime(currentPublished.published_at)}</p>
                </>
              ) : (
                <p className="mt-2 text-sm text-muted-foreground">No published version yet.</p>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={onRefresh}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
            <Button
              onClick={onPublish}
              disabled={publishing || loadingReview || !review?.can_publish}
              className="bg-emerald-600 hover:bg-emerald-700"
            >
              {publishing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Rocket className="mr-2 h-4 w-4" />
              )}
              Publish Draft
            </Button>
            {review && (
              <Badge
                variant="outline"
                className={
                  review.can_publish
                    ? 'border-emerald-500/30 text-emerald-300'
                    : 'border-amber-500/30 text-amber-300'
                }
              >
                {review.can_publish ? 'Ready to publish' : 'Needs attention'}
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Publish Readiness</CardTitle>
          <CardDescription>
            Validation results, missing tools, and deployment blockers for the current draft.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingReview ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading publish review...
            </div>
          ) : !review ? (
            <p className="text-sm text-muted-foreground">No publish review is available for this agent yet.</p>
          ) : (
            <div className="space-y-5">
              <div className="grid gap-3 md:grid-cols-4">
                <div className="rounded-md border border-border p-3">
                  <p className="text-xs text-muted-foreground">Validation</p>
                  <p className="mt-1 text-lg font-semibold">
                    {review.validation.valid ? 'Passing' : 'Failing'}
                  </p>
                </div>
                <div className="rounded-md border border-border p-3">
                  <p className="text-xs text-muted-foreground">Errors</p>
                  <p className="mt-1 text-lg font-semibold text-red-400">
                    {review.validation.error_count}
                  </p>
                </div>
                <div className="rounded-md border border-border p-3">
                  <p className="text-xs text-muted-foreground">Warnings</p>
                  <p className="mt-1 text-lg font-semibold text-amber-300">
                    {review.validation.warning_count}
                  </p>
                </div>
                <div className="rounded-md border border-border p-3">
                  <p className="text-xs text-muted-foreground">Missing tools</p>
                  <p className="mt-1 text-lg font-semibold">
                    {review.missing_tools.length}
                  </p>
                </div>
              </div>

              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-amber-300" />
                  <p className="text-sm font-medium">Blockers</p>
                </div>
                {review.blockers.length === 0 ? (
                  <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 p-3 text-sm text-emerald-200">
                    No publish blockers.
                  </div>
                ) : (
                  review.blockers.map((item, index) => (
                    <div key={`${item.code}-${index}`} className="rounded-md border border-red-500/20 bg-red-500/5 p-3">
                      <p className="text-sm font-medium text-red-200">{item.code}</p>
                      <p className="mt-1 text-xs text-red-100/90">{item.message}</p>
                    </div>
                  ))
                )}
              </div>

              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-300" />
                  <p className="text-sm font-medium">Warnings</p>
                </div>
                {review.warnings.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No warnings.</p>
                ) : (
                  review.warnings.map((item, index) => (
                    <div key={`${item.code}-${index}`} className="rounded-md border border-amber-500/20 bg-amber-500/5 p-3">
                      <p className="text-sm font-medium text-amber-100">{item.code}</p>
                      <p className="mt-1 text-xs text-amber-50/90">{item.message}</p>
                    </div>
                  ))
                )}
              </div>

              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-amber-300" />
                  <p className="text-sm font-medium">Validation Issues</p>
                </div>
                {review.validation.issues.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No validation issues.</p>
                ) : (
                  review.validation.issues.map((issue, index) => {
                    const tone =
                      issue.severity === 'error'
                        ? 'border-red-500/20 bg-red-500/5 text-red-100'
                        : 'border-amber-500/20 bg-amber-500/5 text-amber-50/90'
                    const detail = [issue.step_id ? `state:${issue.step_id}` : null, issue.transition_id ? `transition:${issue.transition_id}` : null]
                      .filter(Boolean)
                      .join(' · ')
                    return (
                      <div key={`${issue.code}-${index}`} className={`rounded-md border p-3 ${tone}`}>
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-medium">{issue.code}</p>
                          <Badge variant="outline" className="capitalize">
                            {issue.severity}
                          </Badge>
                          {detail ? <span className="text-xs opacity-80">{detail}</span> : null}
                        </div>
                        <p className="mt-1 text-xs">{issue.message}</p>
                      </div>
                    )
                  })
                )}
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <p className="text-sm font-medium">Available tools</p>
                  <div className="flex flex-wrap gap-2">
                    {review.available_tools.length > 0 ? (
                      review.available_tools.map((tool) => (
                        <Badge key={tool} variant="outline">{tool}</Badge>
                      ))
                    ) : (
                      <span className="text-sm text-muted-foreground">No tools registered.</span>
                    )}
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium">Missing tools</p>
                  <div className="flex flex-wrap gap-2">
                    {review.missing_tools.length > 0 ? (
                      review.missing_tools.map((tool) => (
                        <Badge key={tool} variant="outline" className="border-red-500/30 text-red-300">
                          {tool}
                        </Badge>
                      ))
                    ) : (
                      <span className="text-sm text-muted-foreground">No missing tools.</span>
                    )}
                  </div>
                </div>
              </div>

              {review.diff && (
                <div className="rounded-md border border-border p-4">
                  <p className="text-sm font-medium">Draft vs live summary</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge variant="outline">+{review.diff.summary.added_steps} steps</Badge>
                    <Badge variant="outline">-{review.diff.summary.removed_steps} steps</Badge>
                    <Badge variant="outline">~{review.diff.summary.changed_steps} steps</Badge>
                    <Badge variant="outline">+{review.diff.summary.added_facts} facts</Badge>
                    <Badge variant="outline">~{review.diff.summary.changed_facts} facts</Badge>
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
