import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { agentDefinitionService } from '@/api/services/agent-definition.service'

interface AgentWidgetViewProps {
  agentId: string
  agentName: string
}

async function copyText(value: string): Promise<void> {
  if (!navigator.clipboard?.writeText) {
    throw new Error('Clipboard is unavailable in this browser context')
  }
  await navigator.clipboard.writeText(value)
}

export function AgentWidgetView({ agentId, agentName }: AgentWidgetViewProps) {
  const configQuery = useQuery({
    queryKey: ['agent-definition-widget-config', agentId],
    queryFn: () => agentDefinitionService.getWidgetConfig(agentId),
    staleTime: 30_000,
  })

  const previewUrl = useMemo(() => `/widget-preview?agent_id=${encodeURIComponent(agentId)}`, [agentId])
  const configUrl = useMemo(() => `/api/v1/public/widget/config?agent_id=${encodeURIComponent(agentId)}`, [agentId])
  const sessionUrl = useMemo(() => `/api/v1/public/widget/sessions`, [])

  const widgetEmbedSnippet = useMemo(
    () =>
      [
        `<script async src="${window.location.origin}/widget.js"></script>`,
        `<script>`,
        `  window.RuhuWidget?.init({`,
        `    agentId: "${agentId}"`,
        `  });`,
        `</script>`,
      ].join('\n'),
    [agentId],
  )

  const config = configQuery.data

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Widget</CardTitle>
          <CardDescription>
            Public widget endpoints and preview for <span className="font-medium text-foreground">{agentName}</span>.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">agent:{agentId}</Badge>
          {config && <Badge variant="outline">position:{config.position}</Badge>}
          {config && <Badge variant="outline">button:{config.button_text}</Badge>}
          <Button
            variant="outline"
            onClick={() => window.open(previewUrl, '_blank', 'noopener,noreferrer')}
          >
            <ExternalLink className="mr-2 h-4 w-4" />
            Open Preview
          </Button>
          <Button
            variant="outline"
            onClick={() => configQuery.refetch()}
            disabled={configQuery.isFetching}
          >
            {configQuery.isFetching && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Refresh Config
          </Button>
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Public Endpoints</CardTitle>
            <CardDescription>Current backend widget endpoints bound to agent ids.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div className="rounded-md border border-border p-3">
              <p className="font-medium">Config</p>
              <p className="mt-1 break-all text-muted-foreground">{configUrl}</p>
              <Button
                variant="ghost"
                size="sm"
                className="mt-2"
                onClick={() =>
                  copyText(configUrl)
                    .then(() => toast.success('Config URL copied'))
                    .catch((error: Error) => toast.error(error.message))
                }
              >
                Copy URL
              </Button>
            </div>

            <div className="rounded-md border border-border p-3">
              <p className="font-medium">Session Create</p>
              <p className="mt-1 break-all text-muted-foreground">POST {sessionUrl}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Request body uses `agent_id` and `channel=\"web_widget\"`.
              </p>
            </div>

            <div className="rounded-md border border-border p-3">
              <p className="font-medium">Preview</p>
              <p className="mt-1 break-all text-muted-foreground">{previewUrl}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Embed Snippet</CardTitle>
            <CardDescription>
              Starter snippet for local and staging embed tests.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <pre className="overflow-x-auto rounded-md border border-border bg-background/50 p-3 text-xs text-muted-foreground">
              {widgetEmbedSnippet}
            </pre>
            <Button
              variant="outline"
              onClick={() =>
                copyText(widgetEmbedSnippet)
                  .then(() => toast.success('Embed snippet copied'))
                  .catch((error: Error) => toast.error(error.message))
              }
            >
              Copy Snippet
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
