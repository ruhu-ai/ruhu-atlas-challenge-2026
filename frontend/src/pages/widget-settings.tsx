/**
 * Widget Settings Page
 *
 * Configure, preview, and embed the widget for an agent.
 * Tabs: Configure | Preview | Embed | Domains | Keys | Analytics
 */

import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import { agentService } from '@/api/services/agent.service'
import {
  widgetService,
  type PublishableKey,
  type PublishableKeyCreated,
} from '@/api/services/widget.service'
import {
  ArrowLeft,
  Copy,
  Check,
  Eye,
  Code,
  Settings as SettingsIcon,
  Globe,
  Key,
  BarChart3,
  Plus,
  Trash2,
  AlertCircle,
} from 'lucide-react'

type WidgetMode = 'chat' | 'voice' | 'multimodal'
type WidgetPosition = 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left'
type BrowserTaskRenderMode = 'hidden' | 'summaries' | 'full'
type BrowserTaskApprovalMode = 'none' | 'explicit' | 'operator_only'
type TabValue = 'configure' | 'preview' | 'embed' | 'domains' | 'keys' | 'analytics'

export function WidgetSettingsContent({ hideBackButton = false }: { hideBackButton?: boolean }) {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // Widget configuration state
  const [widgetMode, setWidgetMode] = useState<WidgetMode>('multimodal')
  const [position, setPosition] = useState<WidgetPosition>('bottom-right')
  const [primaryColor, setPrimaryColor] = useState('#E64E20')
  const [accentColor, setAccentColor] = useState('#D44D00')
  const [buttonText, setButtonText] = useState('Talk to us')
  const [companyName, setCompanyName] = useState('Support')
  const [companyLogo, setCompanyLogo] = useState('')
  const [welcomeMessage, setWelcomeMessage] = useState('Hi! How can I help you today?')
  const [autoOpen, setAutoOpen] = useState(false)
  const [showPoweredBy, setShowPoweredBy] = useState(true)
  const [browserTasksEnabled, setBrowserTasksEnabled] = useState(false)
  const [browserTaskRenderMode, setBrowserTaskRenderMode] = useState<BrowserTaskRenderMode>('hidden')
  const [browserTaskApprovalMode, setBrowserTaskApprovalMode] = useState<BrowserTaskApprovalMode>('operator_only')
  const [browserTaskShowLiveSnapshot, setBrowserTaskShowLiveSnapshot] = useState(false)
  const [browserTaskMaxVisibleArtifacts, setBrowserTaskMaxVisibleArtifacts] = useState(3)
  const [copied, setCopied] = useState(false)
  const [activeTab, setActiveTab] = useState<TabValue>('configure')
  const [previewOpen, setPreviewOpen] = useState(false)

  // Domain management
  const [newDomain, setNewDomain] = useState('')
  const [domainError, setDomainError] = useState('')

  // Key management
  const [newKeyRevealed, setNewKeyRevealed] = useState<string | null>(null)
  const [keyCopied, setKeyCopied] = useState(false)

  // Fetch agent details
  const { data: agent, isLoading } = useQuery({
    queryKey: ['agent', id],
    queryFn: () => agentService.getAgentById(id!),
    enabled: !!id,
  })

  // Fetch publishable keys
  const { data: publishableKeysRaw, refetch: refetchKeys } = useQuery({
    queryKey: ['publishable-keys', id],
    queryFn: () => widgetService.listPublishableKeys(id!).catch(() => [] as PublishableKey[]),
    enabled: !!id,
  })
  const publishableKeys: PublishableKey[] = Array.isArray(publishableKeysRaw) ? publishableKeysRaw : []

  // Fetch embed code
  const { data: embedData } = useQuery({
    queryKey: ['embed-code', id],
    queryFn: () => widgetService.getEmbedCode(id!),
    enabled: !!id && !!agent?.is_widget_enabled,
  })

  // Fetch analytics
  const [analyticsPeriod, setAnalyticsPeriod] = useState('7d')
  const { data: analyticsRaw } = useQuery({
    queryKey: ['widget-analytics', id, analyticsPeriod],
    queryFn: () => widgetService.getWidgetAnalytics(id!, analyticsPeriod).catch(() => null),
    enabled: !!id && activeTab === 'analytics',
  })
  // Guard against empty/partial responses — the backend returns
  // { total_sessions, total_events, event_counts } but the UI expects
  // total_messages, total_voice_calls, unique_visitors, etc.  Derive
  // what we can from event_counts and default the rest to 0.
  const analytics = analyticsRaw?.total_sessions != null
    ? {
        ...analyticsRaw,
        total_messages: analyticsRaw.total_messages ?? analyticsRaw.event_counts?.message ?? 0,
        total_voice_calls: analyticsRaw.total_voice_calls ?? analyticsRaw.event_counts?.voice_start ?? 0,
        unique_visitors: analyticsRaw.unique_visitors ?? analyticsRaw.total_sessions ?? 0,
        avg_session_duration_seconds: analyticsRaw.avg_session_duration_seconds ?? 0,
        avg_messages_per_session: analyticsRaw.avg_messages_per_session ?? 0,
        daily_breakdown: analyticsRaw.daily_breakdown ?? [],
      }
    : null

  // Hydrate local state from agent — only on initial load, not after saves.
  // Using a ref to track whether we've done the initial hydration prevents
  // the useEffect from overwriting user edits when the query re-fetches.
  const hydratedRef = useRef(false)
  useEffect(() => {
    if (agent && !hydratedRef.current) {
      hydratedRef.current = true
      setWidgetMode(agent.widget_mode || 'multimodal')
      const wc = agent.widget_config || {}
      setPosition((wc.position as WidgetPosition) || 'bottom-right')
      setPrimaryColor((wc.primary_color as string) || '#E64E20')
      setAccentColor((wc.accent_color as string) || '#D44D00')
      setButtonText((wc.button_text as string) || 'Talk to us')
      setCompanyName((wc.company_name as string) || agent.name || 'Support')
      setCompanyLogo((wc.company_logo as string) || '')
      setWelcomeMessage((wc.welcome_message as string) || 'Hi! How can I help you today?')
      setAutoOpen(!!wc.auto_open)
      setShowPoweredBy(wc.show_powered_by !== false)
      const features = wc.features && typeof wc.features === 'object' ? wc.features as Record<string, unknown> : {}
      const renderMode = wc.browser_task_render_mode as BrowserTaskRenderMode | undefined
      const approvalMode = wc.browser_task_approval_mode as BrowserTaskApprovalMode | undefined
      const maxArtifacts = Number(wc.browser_task_max_visible_artifacts)
      setBrowserTasksEnabled(features.browser_tasks === true && renderMode !== 'hidden')
      setBrowserTaskRenderMode(renderMode || 'hidden')
      setBrowserTaskApprovalMode(approvalMode || 'operator_only')
      setBrowserTaskShowLiveSnapshot(wc.browser_task_show_live_snapshot === true)
      setBrowserTaskMaxVisibleArtifacts(Number.isFinite(maxArtifacts) && maxArtifacts > 0 ? maxArtifacts : 3)
    }
  }, [agent])

  // --- Mutations ---

  const enableMutation = useMutation({
    mutationFn: () => widgetService.enableWidget(id!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['agent', id] })
      refetchKeys()
      if (data.publishable_key) {
        setNewKeyRevealed(data.publishable_key)
      }
    },
  })

  const disableMutation = useMutation({
    mutationFn: () => widgetService.disableWidget(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent', id] })
    },
  })

  const updateConfigMutation = useMutation({
    mutationFn: () => {
      const existingWidgetConfig = agent?.widget_config || {}
      const existingFeatures =
        existingWidgetConfig.features && typeof existingWidgetConfig.features === 'object'
          ? existingWidgetConfig.features as Record<string, unknown>
          : {}

      return widgetService.updateWidgetConfig(id!, {
        widget_mode: widgetMode,
        widget_config: {
          ...existingWidgetConfig,
          position,
          primary_color: primaryColor,
          accent_color: accentColor,
          button_text: buttonText,
          company_name: companyName,
          company_logo: companyLogo || undefined,
          welcome_message: welcomeMessage,
          auto_open: autoOpen,
          show_powered_by: showPoweredBy,
          features: {
            ...existingFeatures,
            browser_tasks: browserTasksEnabled,
          },
          browser_task_render_mode: browserTasksEnabled ? browserTaskRenderMode : 'hidden',
          browser_task_approval_mode: browserTaskApprovalMode,
          browser_task_show_live_snapshot:
            browserTasksEnabled && browserTaskRenderMode === 'full' && browserTaskShowLiveSnapshot,
          browser_task_max_visible_artifacts: Math.max(0, Math.min(10, browserTaskMaxVisibleArtifacts)),
        },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent', id] })
      queryClient.invalidateQueries({ queryKey: ['embed-code', id] })
    },
  })

  const createKeyMutation = useMutation({
    mutationFn: () =>
      widgetService.createPublishableKey({
        name: 'Live widget key',
        agent_id: id!,
        environment: 'live',
      }),
    onSuccess: (data: PublishableKeyCreated) => {
      setNewKeyRevealed(data.key)
      refetchKeys()
      queryClient.invalidateQueries({ queryKey: ['embed-code', id] })
    },
  })

  const revokeKeyMutation = useMutation({
    mutationFn: (keyId: string) => widgetService.revokeKey(keyId),
    onSuccess: () => {
      refetchKeys()
      queryClient.invalidateQueries({ queryKey: ['embed-code', id] })
    },
  })

  const deleteKeyMutation = useMutation({
    mutationFn: (keyId: string) => widgetService.deleteKeyPermanent(keyId),
    onSuccess: () => {
      refetchKeys()
    },
  })

  // --- Handlers ---

  const handleCreateKey = useCallback(() => {
    if (window.confirm('Create a new publishable key? The full key will only be shown once.')) {
      createKeyMutation.mutate()
    }
  }, [createKeyMutation])

  const handleToggleWidget = useCallback(() => {
    if (agent?.is_widget_enabled) {
      disableMutation.mutate()
    } else {
      enableMutation.mutate()
    }
  }, [agent?.is_widget_enabled, enableMutation, disableMutation])

  const handleSaveConfig = useCallback(() => {
    updateConfigMutation.mutate()
  }, [updateConfigMutation])

  const handleBrowserTasksEnabledChange = useCallback((enabled: boolean) => {
    setBrowserTasksEnabled(enabled)
    setBrowserTaskRenderMode((current) => {
      if (!enabled) return 'hidden'
      return current === 'hidden' ? 'summaries' : current
    })
  }, [])

  const handleBrowserTaskMaxArtifactsChange = useCallback((value: string) => {
    const parsed = Number.parseInt(value, 10)
    if (!Number.isFinite(parsed)) {
      setBrowserTaskMaxVisibleArtifacts(0)
      return
    }
    setBrowserTaskMaxVisibleArtifacts(Math.max(0, Math.min(10, parsed)))
  }, [])

  const handleAddDomain = useCallback(
    (keyId: string, currentOrigins: string[]) => {
      const domain = newDomain.trim()
      if (!domain) return

      // Basic validation
      if (!domain.startsWith('https://') && !domain.startsWith('http://') && !domain.startsWith('*.')) {
        setDomainError('Domain must start with https://, http://, or *. for wildcard')
        return
      }
      if (currentOrigins.includes(domain)) {
        setDomainError('Domain already added')
        return
      }

      setDomainError('')
      widgetService
        .updateKeyOrigins(keyId, [...currentOrigins, domain])
        .then(() => {
          setNewDomain('')
          refetchKeys()
        })
        .catch((err) => setDomainError(err.message))
    },
    [newDomain, refetchKeys],
  )

  const handleRemoveDomain = useCallback(
    (keyId: string, currentOrigins: string[], domain: string) => {
      widgetService
        .updateKeyOrigins(
          keyId,
          currentOrigins.filter((o) => o !== domain),
        )
        .then(() => refetchKeys())
    },
    [refetchKeys],
  )

  const handleCopyKey = useCallback((key: string) => {
    navigator.clipboard.writeText(key)
    setKeyCopied(true)
    setTimeout(() => setKeyCopied(false), 2000)
  }, [])

  // Escape HTML attributes for embed code display
  const escapeHtml = (str: string) =>
    str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')

  const keyPlaceholder = embedData?.key_placeholder || 'REPLACE_WITH_PUBLISHABLE_KEY'
  const embedKeyForDisplay = newKeyRevealed || keyPlaceholder

  // Generate local embed code preview
  const localEmbedCode = `<!-- Ruhu Widget -->
<script
  src="${embedData?.widget_url || 'https://app.ruhu.ai/widget/widget.js'}"
  data-widget-key="${embedKeyForDisplay}"
  data-position="${position}"
  data-primary-color="${escapeHtml(primaryColor)}"
  data-accent-color="${escapeHtml(accentColor)}"
  data-button-text="${escapeHtml(buttonText)}"
  data-company-name="${escapeHtml(companyName)}"${companyLogo ? `\n  data-company-logo="${escapeHtml(companyLogo)}"` : ''}
  data-welcome-message="${escapeHtml(welcomeMessage)}"${autoOpen ? '\n  data-auto-open="true"' : ''}
></script>`

  const resolvedEmbedCode =
    embedData?.embed_code?.split(keyPlaceholder).join(embedKeyForDisplay) || localEmbedCode

  const embedCodeNeedsRealKey = resolvedEmbedCode.includes(keyPlaceholder)

  const handleCopyEmbed = useCallback(() => {
    navigator.clipboard.writeText(resolvedEmbedCode)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [resolvedEmbedCode])

  // Active key for domain management — defaults to first but user can select any
  const [selectedKeyId, setSelectedKeyId] = useState<string | null>(null)
  const activeKey = publishableKeys.find((k) => k.id === selectedKeyId) || publishableKeys[0] || null

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <p className="text-muted-foreground">Loading widget settings...</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          {!hideBackButton && (
            <Button variant="ghost" size="icon" onClick={() => navigate(`/agents/${id}`)}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}
          <div>
            <h1 className="text-2xl font-bold">Widget Settings</h1>
            <p className="text-sm text-muted-foreground">
              Configure and embed your AI widget for {agent?.name}
            </p>
          </div>
        </div>

          {/* Enable/Disable Toggle */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">
              {agent?.is_widget_enabled ? 'Widget enabled' : 'Widget disabled'}
            </span>
            <Button
              variant={agent?.is_widget_enabled ? 'destructive' : 'primary'}
              size="sm"
              onClick={handleToggleWidget}
              disabled={
                enableMutation.isPending ||
                disableMutation.isPending ||
                (!agent?.is_widget_enabled && !['active', 'deployed', 'published'].includes(agent?.status || ''))
              }
            >
              {agent?.is_widget_enabled ? 'Disable' : 'Enable'}
            </Button>
          </div>
        </div>

        {/* Not enabled warning */}
        {!agent?.is_widget_enabled && (
          <div className="rounded-lg border border-border bg-muted/40 p-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-muted-foreground mt-0.5 flex-shrink-0" />
              <div>
                <h4 className="font-medium text-foreground">Widget Not Enabled</h4>
                <p className="text-sm text-muted-foreground mt-1">
                  Enable the widget to generate a publishable key and embed code. Your agent must
                  have an active canvas version and be in active, deployed, or published status.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Enable error */}
        {enableMutation.isError && (
          <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
            <p className="text-sm text-red-400">
              {enableMutation.error instanceof Error
                ? enableMutation.error.message
                : 'Failed to enable widget'}
            </p>
          </div>
        )}

        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as TabValue)}
          className="space-y-4"
        >
          <TabsList className="grid w-full grid-cols-6">
            <TabsTrigger value="configure">
              <SettingsIcon className="mr-2 h-4 w-4" />
              Configure
            </TabsTrigger>
            <TabsTrigger value="preview">
              <Eye className="mr-2 h-4 w-4" />
              Preview
            </TabsTrigger>
            <TabsTrigger value="embed">
              <Code className="mr-2 h-4 w-4" />
              Embed
            </TabsTrigger>
            <TabsTrigger value="domains">
              <Globe className="mr-2 h-4 w-4" />
              Domains
            </TabsTrigger>
            <TabsTrigger value="keys">
              <Key className="mr-2 h-4 w-4" />
              Keys
            </TabsTrigger>
            <TabsTrigger value="analytics">
              <BarChart3 className="mr-2 h-4 w-4" />
              Analytics
            </TabsTrigger>
          </TabsList>

          {/* ========== Configure Tab ========== */}
          <TabsContent value="configure" className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-2">
              {/* Mode & Appearance */}
              <Card className="glass-card">
                <CardHeader>
                  <CardTitle>Mode & Appearance</CardTitle>
                  <CardDescription>Widget mode and visual settings</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="widget-mode">Widget Mode</Label>
                    <Select value={widgetMode} onValueChange={(v) => setWidgetMode(v as WidgetMode)}>
                      <SelectTrigger id="widget-mode">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="chat">Chat Only</SelectItem>
                        <SelectItem value="voice">Voice Only</SelectItem>
                        <SelectItem value="multimodal">Multimodal (Chat + Voice)</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      Determines which interaction modes are available to end users
                    </p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="position">Position</Label>
                    <Select value={position} onValueChange={(v) => setPosition(v as WidgetPosition)}>
                      <SelectTrigger id="position">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="bottom-right">Bottom Right</SelectItem>
                        <SelectItem value="bottom-left">Bottom Left</SelectItem>
                        <SelectItem value="top-right">Top Right</SelectItem>
                        <SelectItem value="top-left">Top Left</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="primary-color">Primary Color</Label>
                      <div className="flex gap-2">
                        <Input
                          id="primary-color"
                          type="color"
                          value={primaryColor}
                          onChange={(e) => setPrimaryColor(e.target.value)}
                          className="h-10 w-20"
                        />
                        <Input
                          value={primaryColor}
                          onChange={(e) => setPrimaryColor(e.target.value)}
                          placeholder="#E64E20"
                        />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="accent-color">Accent Color</Label>
                      <div className="flex gap-2">
                        <Input
                          id="accent-color"
                          type="color"
                          value={accentColor}
                          onChange={(e) => setAccentColor(e.target.value)}
                          className="h-10 w-20"
                        />
                        <Input
                          value={accentColor}
                          onChange={(e) => setAccentColor(e.target.value)}
                          placeholder="#D44D00"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="company-logo">Company Logo URL (optional)</Label>
                    <Input
                      id="company-logo"
                      value={companyLogo}
                      onChange={(e) => setCompanyLogo(e.target.value)}
                      placeholder="https://example.com/logo.png"
                    />
                  </div>
                </CardContent>
              </Card>

              {/* Content Settings */}
              <Card className="glass-card">
                <CardHeader>
                  <CardTitle>Content</CardTitle>
                  <CardDescription>Configure widget text and messaging</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="button-text">Button Text</Label>
                    <Input
                      id="button-text"
                      value={buttonText}
                      onChange={(e) => setButtonText(e.target.value)}
                      placeholder="Talk to us"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="company-name">Company Name</Label>
                    <Input
                      id="company-name"
                      value={companyName}
                      onChange={(e) => setCompanyName(e.target.value)}
                      placeholder="Support"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="welcome-message">Welcome Message</Label>
                    <Textarea
                      id="welcome-message"
                      value={welcomeMessage}
                      onChange={(e) => setWelcomeMessage(e.target.value)}
                      placeholder="Hi! How can I help you today?"
                      rows={3}
                    />
                  </div>

                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="auto-open"
                      checked={autoOpen}
                      onChange={(e) => setAutoOpen(e.target.checked)}
                      className="rounded"
                    />
                    <Label htmlFor="auto-open" className="cursor-pointer">
                      Auto-open widget on page load
                    </Label>
                  </div>

                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="show-powered-by"
                      checked={showPoweredBy}
                      onChange={(e) => setShowPoweredBy(e.target.checked)}
                      className="rounded"
                    />
                    <Label htmlFor="show-powered-by" className="cursor-pointer">
                      Show "Powered by Ruhu" footer
                    </Label>
                  </div>
                </CardContent>
              </Card>

              {/* Browser Agent */}
              <Card className="glass-card">
                <CardHeader>
                  <CardTitle>Browser Agent</CardTitle>
                  <CardDescription>Customer-facing browser task visibility</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="browser-tasks-enabled"
                      checked={browserTasksEnabled}
                      onChange={(e) => handleBrowserTasksEnabledChange(e.target.checked)}
                      className="rounded"
                    />
                    <Label htmlFor="browser-tasks-enabled" className="cursor-pointer">
                      Show browser task progress in the widget
                    </Label>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="browser-task-render-mode">Display Mode</Label>
                    <Select
                      value={browserTasksEnabled ? browserTaskRenderMode : 'hidden'}
                      onValueChange={(v) => setBrowserTaskRenderMode(v as BrowserTaskRenderMode)}
                      disabled={!browserTasksEnabled}
                    >
                      <SelectTrigger id="browser-task-render-mode">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="summaries">Summaries</SelectItem>
                        <SelectItem value="hidden">Hidden</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="browser-task-approval-mode">Approval Mode</Label>
                    <Select
                      value={browserTaskApprovalMode}
                      onValueChange={(v) => setBrowserTaskApprovalMode(v as BrowserTaskApprovalMode)}
                      disabled={!browserTasksEnabled}
                    >
                      <SelectTrigger id="browser-task-approval-mode">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="operator_only">Operator Only</SelectItem>
                        <SelectItem value="explicit">Customer Approval</SelectItem>
                        <SelectItem value="none">No Customer Approval</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="browser-task-max-artifacts">Visible Artifacts</Label>
                      <Input
                        id="browser-task-max-artifacts"
                        type="number"
                        min={0}
                        max={10}
                        value={browserTaskMaxVisibleArtifacts}
                        disabled={!browserTasksEnabled}
                        onChange={(e) => handleBrowserTaskMaxArtifactsChange(e.target.value)}
                      />
                    </div>
                    <div className="flex items-end pb-2">
                      <div className="flex items-center space-x-2">
                        <input
                          type="checkbox"
                          id="browser-task-live-snapshot"
                          checked={browserTaskShowLiveSnapshot}
                          onChange={(e) => setBrowserTaskShowLiveSnapshot(e.target.checked)}
                          disabled={!browserTasksEnabled || browserTaskRenderMode !== 'full'}
                          className="rounded"
                        />
                        <Label htmlFor="browser-task-live-snapshot" className="cursor-pointer">
                          Live snapshot
                        </Label>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Agent Info */}
            <Card className="glass-card">
              <CardHeader>
                <CardTitle>Agent Information</CardTitle>
                <CardDescription>The AI agent powering this widget</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Agent:</span>
                  <span className="text-sm text-muted-foreground">{agent?.name || 'Unknown'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Type:</span>
                  <span className="text-sm text-muted-foreground capitalize">{agent?.agent_type}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Status:</span>
                  <span
                    className={`text-sm ${
                      agent?.status === 'active' || agent?.status === 'deployed'
                        ? 'text-emerald-400'
                        : 'text-muted-foreground'
                    }`}
                  >
                    {agent?.status || 'draft'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Widget:</span>
                  <span
                    className={`text-sm ${agent?.is_widget_enabled ? 'text-emerald-400' : 'text-muted-foreground'}`}
                  >
                    {agent?.is_widget_enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
              </CardContent>
            </Card>

            {/* Save Button */}
            <div className="flex justify-end">
              <Button
                onClick={handleSaveConfig}
                disabled={updateConfigMutation.isPending}
              >
                {updateConfigMutation.isPending ? 'Saving...' : 'Save Configuration'}
              </Button>
            </div>
          </TabsContent>

          {/* ========== Preview Tab ========== */}
          <TabsContent value="preview" className="space-y-4">
            <Card className="glass-card">
              <CardHeader>
                <CardTitle>Widget Preview</CardTitle>
                <CardDescription>
                  Click the widget button to see the expanded view. This reflects your current configuration.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="relative h-[600px] bg-gradient-to-br from-gray-100 to-gray-200 dark:from-gray-800 dark:to-gray-900 rounded-lg overflow-hidden">
                  {/* Mock Website Background */}
                  <div className="absolute inset-0 p-8">
                    {/* Nav bar */}
                    <div className="flex items-center gap-4 mb-6">
                      <div className="h-8 w-8 rounded-full bg-gray-300 dark:bg-gray-600" />
                      <div className="h-4 bg-gray-300 dark:bg-gray-600 rounded w-24" />
                      <div className="flex-1" />
                      <div className="h-4 bg-gray-200 dark:bg-gray-600/50 rounded w-16" />
                      <div className="h-4 bg-gray-200 dark:bg-gray-600/50 rounded w-16" />
                      <div className="h-4 bg-gray-200 dark:bg-gray-600/50 rounded w-16" />
                    </div>
                    {/* Hero section */}
                    <div className="bg-white dark:bg-gray-700 rounded-lg shadow-lg p-6 mb-4">
                      <div className="h-6 bg-gray-200 dark:bg-gray-600 rounded w-1/3 mb-4" />
                      <div className="h-4 bg-gray-100 dark:bg-gray-600/50 rounded w-full mb-2" />
                      <div className="h-4 bg-gray-100 dark:bg-gray-600/50 rounded w-5/6 mb-2" />
                      <div className="h-4 bg-gray-100 dark:bg-gray-600/50 rounded w-4/5" />
                    </div>
                    {/* Content cards */}
                    <div className="grid grid-cols-3 gap-4">
                      {[1, 2, 3].map((i) => (
                        <div key={i} className="bg-white dark:bg-gray-700 rounded-lg shadow-lg p-4">
                          <div className="h-20 bg-gray-100 dark:bg-gray-600/30 rounded mb-3" />
                          <div className="h-4 bg-gray-200 dark:bg-gray-600 rounded w-2/3 mb-2" />
                          <div className="h-3 bg-gray-100 dark:bg-gray-600/50 rounded w-full" />
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Expanded Chat Panel */}
                  {previewOpen && (
                    <div
                      className={`absolute ${position.includes('right') ? 'right-5' : 'left-5'} ${position.includes('bottom') ? 'bottom-20' : 'top-20'} w-[360px] h-[420px] rounded-2xl shadow-2xl overflow-hidden flex flex-col border border-white/10`}
                      style={{ background: '#1a1a2e' }}
                    >
                      {/* Chat Header */}
                      <div
                        className="px-5 py-4 flex items-center gap-3"
                        style={{ background: `linear-gradient(135deg, ${primaryColor}, ${accentColor})` }}
                      >
                        {companyLogo ? (
                          <img src={companyLogo} alt="" className="h-9 w-9 rounded-full object-cover bg-white/20" />
                        ) : (
                          <div className="h-9 w-9 rounded-full bg-white/20 flex items-center justify-center text-white font-bold text-sm">
                            {companyName.charAt(0).toUpperCase()}
                          </div>
                        )}
                        <div className="flex-1">
                          <div className="text-white font-semibold text-sm">{companyName}</div>
                          <div className="text-white/70 text-xs flex items-center gap-1">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 inline-block" />
                            Online
                          </div>
                        </div>
                        <button
                          onClick={() => setPreviewOpen(false)}
                          className="text-white/70 hover:text-white transition-colors"
                        >
                          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>

                      {/* Chat Body */}
                      <div className="flex-1 p-4 space-y-3 overflow-y-auto">
                        {/* Welcome message bubble */}
                        <div className="flex items-start gap-2">
                          <div
                            className="h-7 w-7 rounded-full flex-shrink-0 flex items-center justify-center text-white text-xs font-bold"
                            style={{ background: primaryColor }}
                          >
                            {companyName.charAt(0).toUpperCase()}
                          </div>
                          <div className="bg-white/10 rounded-2xl rounded-tl-sm px-4 py-2.5 max-w-[260px]">
                            <p className="text-white/90 text-sm">{welcomeMessage}</p>
                          </div>
                        </div>

                        {/* Mode indicators */}
                        {(widgetMode === 'voice' || widgetMode === 'multimodal') && (
                          <div className="flex items-center gap-2 px-2">
                            <div className="flex gap-0.5">
                              {[1, 2, 3, 4, 5].map((i) => (
                                <div
                                  key={i}
                                  className="w-1 rounded-full animate-pulse"
                                  style={{
                                    height: `${8 + Math.random() * 12}px`,
                                    background: primaryColor,
                                    opacity: 0.4 + Math.random() * 0.4,
                                    animationDelay: `${i * 0.1}s`,
                                  }}
                                />
                              ))}
                            </div>
                            <span className="text-white/40 text-xs">Voice enabled</span>
                          </div>
                        )}
                      </div>

                      {/* Chat Input */}
                      <div className="p-3 border-t border-white/10">
                        {(widgetMode === 'chat' || widgetMode === 'multimodal') && (
                          <div className="flex items-center gap-2">
                            <div className="flex-1 bg-white/10 rounded-full px-4 py-2.5 text-white/40 text-sm">
                              Type a message...
                            </div>
                            {widgetMode === 'multimodal' && (
                              <button
                                className="h-9 w-9 rounded-full flex items-center justify-center text-white/60"
                                style={{ background: `${primaryColor}30` }}
                              >
                                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
                                  <path d="M19 10v2a7 7 0 01-14 0v-2" />
                                  <line x1="12" y1="19" x2="12" y2="23" />
                                  <line x1="8" y1="23" x2="16" y2="23" />
                                </svg>
                              </button>
                            )}
                            <button
                              className="h-9 w-9 rounded-full flex items-center justify-center text-white"
                              style={{ background: primaryColor }}
                            >
                              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="22" y1="2" x2="11" y2="13" />
                                <polygon points="22 2 15 22 11 13 2 9 22 2" />
                              </svg>
                            </button>
                          </div>
                        )}
                        {widgetMode === 'voice' && (
                          <div className="flex flex-col items-center gap-2 py-2">
                            <button
                              className="h-14 w-14 rounded-full flex items-center justify-center text-white shadow-lg"
                              style={{ background: `linear-gradient(135deg, ${primaryColor}, ${accentColor})` }}
                            >
                              <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
                                <path d="M19 10v2a7 7 0 01-14 0v-2" />
                                <line x1="12" y1="19" x2="12" y2="23" />
                                <line x1="8" y1="23" x2="16" y2="23" />
                              </svg>
                            </button>
                            <span className="text-white/40 text-xs">Tap to speak</span>
                          </div>
                        )}
                        {showPoweredBy && (
                          <div className="text-center mt-2">
                            <span className="text-white/30 text-[10px]">Powered by Ruhu</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Widget FAB Preview */}
                  <div
                    className={`absolute ${position === 'bottom-right' ? 'bottom-5 right-5' : position === 'bottom-left' ? 'bottom-5 left-5' : position === 'top-right' ? 'top-5 right-5' : 'top-5 left-5'}`}
                  >
                    <button
                      onClick={() => setPreviewOpen(!previewOpen)}
                      className="flex items-center gap-3 rounded-full px-6 py-4 text-white font-medium shadow-2xl transition-transform hover:scale-105"
                      style={{
                        background: `linear-gradient(135deg, ${primaryColor}, ${accentColor})`,
                      }}
                    >
                      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        {previewOpen ? (
                          <path d="M6 18L18 6M6 6l12 12" />
                        ) : widgetMode === 'voice' ? (
                          <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72 12.84 12.84 0 00.7 2.81 2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45 12.84 12.84 0 002.81.7A2 2 0 0122 16.92z" />
                        ) : (
                          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
                        )}
                      </svg>
                      <span>{previewOpen ? 'Close' : buttonText}</span>
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-3 gap-4 text-center text-sm text-muted-foreground">
                  <div>
                    <div className="font-medium text-foreground capitalize">{widgetMode}</div>
                    <div>Mode</div>
                  </div>
                  <div>
                    <div className="font-medium text-foreground capitalize">{position.replace('-', ' ')}</div>
                    <div>Position</div>
                  </div>
                  <div>
                    <div
                      className="h-6 w-6 rounded-full mx-auto mb-1"
                      style={{ background: primaryColor }}
                    />
                    <div>Color</div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ========== Embed Tab ========== */}
          <TabsContent value="embed" className="space-y-4">
            <Card className="glass-card">
              <CardHeader>
                <CardTitle>Embed Code</CardTitle>
                <CardDescription>
                  Copy and paste this code into your website's HTML, just before the closing &lt;/body&gt; tag
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {!agent?.is_widget_enabled ? (
                  <div className="rounded-lg border border-border bg-muted/40 p-6 text-center">
                    <AlertCircle className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
                    <p className="text-sm text-muted-foreground">
                      Enable the widget first to generate your embed code.
                    </p>
                  </div>
                ) : (
                  <>
                    <div className="relative">
                      <pre className="bg-gray-900 text-gray-100 p-4 rounded-lg overflow-x-auto text-sm">
                        <code>{resolvedEmbedCode}</code>
                      </pre>
                      <Button
                        onClick={handleCopyEmbed}
                        size="sm"
                        className="absolute top-2 right-2"
                        variant="outline"
                      >
                        {copied ? (
                          <>
                            <Check className="mr-2 h-4 w-4" />
                            Copied!
                          </>
                        ) : (
                          <>
                            <Copy className="mr-2 h-4 w-4" />
                            Copy
                          </>
                        )}
                      </Button>
                    </div>

                    <div className="space-y-3">
                      <h4 className="font-medium">Installation Steps:</h4>
                      <ol className="list-decimal list-inside space-y-2 text-sm text-muted-foreground">
                        <li>Copy the embed code above</li>
                        <li>Open your website's HTML file</li>
                        <li>Paste the code just before the closing &lt;/body&gt; tag</li>
                        <li>Add your domain to the allowed domains list (Domains tab)</li>
                        <li>Save and deploy your website</li>
                        <li>The widget will appear automatically on all pages</li>
                      </ol>
                    </div>

                    <div className="rounded-lg border border-primary/20 bg-primary/5 p-4">
                      <h4 className="font-medium text-primary mb-2">Widget CDN</h4>
                      <p className="text-sm text-muted-foreground mb-2">
                        The widget is served from our global CDN for fast loading worldwide:
                      </p>
                      <code className="text-xs bg-black/20 px-2 py-1 rounded block">
                        {embedData?.widget_url || 'https://app.ruhu.ai/widget/widget.js'}
                      </code>
                    </div>

                    {embedCodeNeedsRealKey && (
                      <div className="rounded-lg border border-border bg-muted/40 p-4">
                        <div className="flex items-start gap-3">
                          <AlertCircle className="h-5 w-5 text-muted-foreground mt-0.5 flex-shrink-0" />
                          <div>
                            <h4 className="font-medium text-foreground">Publishable Key Required</h4>
                            <p className="text-sm text-muted-foreground mt-1">
                              Replace <code className="text-xs bg-black/20 px-1 py-0.5 rounded">{keyPlaceholder}</code>{' '}
                              with a full publishable key value. Key prefixes (for example{' '}
                              <code className="text-xs bg-black/20 px-1 py-0.5 rounded">
                                {embedData?.publishable_key_prefix || 'pk_live_xxx'}
                              </code>
                              ) cannot be used for runtime authentication.
                            </p>
                          </div>
                        </div>
                      </div>
                    )}
                  </>
                )}

                {/* Agent status warning */}
                {agent?.status !== 'active' && agent?.status !== 'deployed' && (
                  <div className="rounded-lg border border-border bg-muted/40 p-4">
                    <div className="flex items-start gap-3">
                      <AlertCircle className="h-5 w-5 text-muted-foreground mt-0.5 flex-shrink-0" />
                      <div>
                        <h4 className="font-medium text-foreground">Agent Not Live</h4>
                        <p className="text-sm text-muted-foreground">
                          Your agent is in "{agent?.status}" status. Deploy your agent to make the
                          widget functional.
                        </p>
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ========== Domains Tab ========== */}
          <TabsContent value="domains" className="space-y-4">
            <Card className="glass-card">
              <CardHeader>
                <CardTitle>Allowed Domains</CardTitle>
                <CardDescription>
                  Restrict which domains can use your widget. Only requests from these origins will be accepted.
                  Use <code className="text-xs bg-muted px-1 py-0.5 rounded">*.example.com</code> for wildcard subdomains.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {!activeKey ? (
                  <div className="rounded-lg border border-muted bg-muted/30 p-6 text-center">
                    <Globe className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
                    <p className="text-sm text-muted-foreground">
                      Enable the widget and create a publishable key to manage domains.
                    </p>
                  </div>
                ) : (
                  <>
                    {/* Key selector when multiple active keys exist */}
                    {publishableKeys.filter((k) => k.is_active).length > 1 && (
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-muted-foreground">Key:</span>
                        <Select
                          value={activeKey.id}
                          onValueChange={(val) => setSelectedKeyId(val)}
                        >
                          <SelectTrigger className="w-64">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {publishableKeys.filter((k) => k.is_active).map((k) => (
                              <SelectItem key={k.id} value={k.id}>
                                {k.key_prefix}... ({k.allowed_origins.length} domain{k.allowed_origins.length !== 1 ? 's' : ''})
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    )}

                    {/* Current domains */}
                    <div className="space-y-2">
                      {activeKey.allowed_origins.length === 0 ? (
                        <div className="rounded-lg border border-border bg-muted/40 p-4">
                          <div className="flex items-start gap-3">
                            <AlertCircle className="h-5 w-5 text-muted-foreground mt-0.5 flex-shrink-0" />
                            <div>
                              <p className="text-sm text-foreground font-medium">No domains configured</p>
                              <p className="text-xs text-muted-foreground mt-1">
                                All origins are allowed. Add domains to restrict widget usage to specific websites.
                              </p>
                            </div>
                          </div>
                        </div>
                      ) : (
                        activeKey.allowed_origins.map((origin) => (
                          <div
                            key={origin}
                            className="flex items-center justify-between rounded-lg border px-4 py-3"
                          >
                            <div className="flex items-center gap-2">
                              <Globe className="h-4 w-4 text-muted-foreground" />
                              <code className="text-sm">{origin}</code>
                            </div>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-muted-foreground hover:text-red-400"
                              onClick={() =>
                                handleRemoveDomain(activeKey.id, activeKey.allowed_origins, origin)
                              }
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        ))
                      )}
                    </div>

                    {/* Add domain */}
                    <div className="flex gap-2">
                      <Input
                        value={newDomain}
                        onChange={(e) => {
                          setNewDomain(e.target.value)
                          setDomainError('')
                        }}
                        placeholder="https://example.com"
                        className="flex-1"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            handleAddDomain(activeKey.id, activeKey.allowed_origins)
                          }
                        }}
                      />
                      <Button
                        onClick={() => handleAddDomain(activeKey.id, activeKey.allowed_origins)}
                      >
                        <Plus className="mr-2 h-4 w-4" />
                        Add
                      </Button>
                    </div>
                    {domainError && (
                      <p className="text-sm text-red-400">{domainError}</p>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ========== Keys Tab ========== */}
          <TabsContent value="keys" className="space-y-4">
            <Card className="glass-card">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Publishable Keys</CardTitle>
                    <CardDescription>
                      Keys used in the embed snippet. Safe to expose in client-side code — scoped to
                      specific agents and domains.
                    </CardDescription>
                  </div>
                  <Button
                    size="sm"
                    onClick={handleCreateKey}
                    disabled={createKeyMutation.isPending}
                  >
                    <Plus className="mr-2 h-4 w-4" />
                    Create Key
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* New key reveal */}
                {newKeyRevealed && (
                  <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-4">
                    <div className="flex items-start gap-3">
                      <Check className="h-5 w-5 text-emerald-400 mt-0.5 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-emerald-300 mb-2">
                          Key created! Copy it now — it won't be shown again.
                        </p>
                        <div className="flex items-center gap-2">
                          <code className="text-xs bg-black/20 px-2 py-1 rounded block flex-1 overflow-hidden text-ellipsis">
                            {newKeyRevealed}
                          </code>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleCopyKey(newKeyRevealed)}
                          >
                            {keyCopied ? (
                              <Check className="h-4 w-4" />
                            ) : (
                              <Copy className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Key list */}
                {publishableKeys.length === 0 ? (
                  <div className="rounded-lg border border-muted bg-muted/30 p-6 text-center">
                    <Key className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
                    <p className="text-sm text-muted-foreground">
                      No publishable keys yet. Create one to embed the widget.
                    </p>
                  </div>
                ) : (
                  publishableKeys.map((key) => (
                    <div
                      key={key.id}
                      className="flex items-center justify-between rounded-lg border px-4 py-3"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <code className="text-sm font-mono">{key.key_prefix}...</code>
                          <span
                            className={`text-xs px-2 py-0.5 rounded-full ${
                              key.is_active
                                ? 'bg-emerald-500/10 text-emerald-400'
                                : 'bg-red-500/10 text-red-400'
                            }`}
                          >
                            {key.is_active ? 'Active' : 'Revoked'}
                          </span>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-muted-foreground">
                          <span>Created {new Date(key.created_at).toLocaleDateString()}</span>
                          <button
                            className="underline hover:text-foreground"
                            onClick={() => {
                              setSelectedKeyId(key.id)
                              // Switch to Domains tab — find the Tabs component and set value
                              const domainsTab = document.querySelector('[data-value="domains"]') as HTMLElement | null
                              domainsTab?.click()
                            }}
                          >
                            {key.allowed_origins.length} domain
                            {key.allowed_origins.length !== 1 ? 's' : ''}
                          </button>
                          {key.last_used_at && (
                            <span>Last used {new Date(key.last_used_at).toLocaleDateString()}</span>
                          )}
                        </div>
                      </div>
                      {key.is_active ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                          onClick={() => {
                            if (window.confirm('Revoke this key? Any widgets using it will stop working immediately.')) {
                              revokeKeyMutation.mutate(key.id)
                            }
                          }}
                          disabled={revokeKeyMutation.isPending}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
                          onClick={() => {
                            if (window.confirm('Permanently delete this revoked key? This cannot be undone.')) {
                              deleteKeyMutation.mutate(key.id)
                            }
                          }}
                          disabled={deleteKeyMutation.isPending}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ========== Analytics Tab ========== */}
          <TabsContent value="analytics" className="space-y-4">
            <Card className="glass-card">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Widget Analytics</CardTitle>
                    <CardDescription>Usage metrics for your embedded widget</CardDescription>
                  </div>
                  <Select value={analyticsPeriod} onValueChange={setAnalyticsPeriod}>
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="24h">Last 24h</SelectItem>
                      <SelectItem value="7d">Last 7 days</SelectItem>
                      <SelectItem value="30d">Last 30 days</SelectItem>
                      <SelectItem value="90d">Last 90 days</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </CardHeader>
              <CardContent>
                {!analytics ? (
                  <div className="rounded-lg border border-muted bg-muted/30 p-6 text-center">
                    <BarChart3 className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
                    <p className="text-sm text-muted-foreground">
                      {agent?.is_widget_enabled
                        ? 'No analytics data yet. Data will appear once your widget receives traffic.'
                        : 'Enable the widget to start collecting analytics.'}
                    </p>
                  </div>
                ) : (
                  <div className="space-y-6">
                    {/* Summary stats */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                      <div className="rounded-lg border p-4">
                        <div className="text-2xl font-bold">{analytics.total_sessions.toLocaleString()}</div>
                        <div className="text-sm text-muted-foreground">Total Sessions</div>
                      </div>
                      <div className="rounded-lg border p-4">
                        <div className="text-2xl font-bold">{analytics.total_messages.toLocaleString()}</div>
                        <div className="text-sm text-muted-foreground">Total Messages</div>
                      </div>
                      <div className="rounded-lg border p-4">
                        <div className="text-2xl font-bold">{analytics.total_voice_calls.toLocaleString()}</div>
                        <div className="text-sm text-muted-foreground">Voice Calls</div>
                      </div>
                      <div className="rounded-lg border p-4">
                        <div className="text-2xl font-bold">{analytics.unique_visitors.toLocaleString()}</div>
                        <div className="text-sm text-muted-foreground">Unique Visitors</div>
                      </div>
                    </div>

                    {/* Secondary stats */}
                    <div className="grid grid-cols-2 gap-4">
                      <div className="rounded-lg border p-4">
                        <div className="text-lg font-semibold">
                          {Math.round(analytics.avg_session_duration_seconds / 60)}m{' '}
                          {analytics.avg_session_duration_seconds % 60}s
                        </div>
                        <div className="text-sm text-muted-foreground">Avg Session Duration</div>
                      </div>
                      <div className="rounded-lg border p-4">
                        <div className="text-lg font-semibold">
                          {analytics.avg_messages_per_session.toFixed(1)}
                        </div>
                        <div className="text-sm text-muted-foreground">Avg Messages/Session</div>
                      </div>
                    </div>

                    {/* Daily breakdown table */}
                    {analytics.daily_breakdown.length > 0 && (
                      <div>
                        <h4 className="font-medium mb-3">Daily Breakdown</h4>
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="border-b">
                                <th className="text-left py-2 px-3 text-muted-foreground font-medium">Date</th>
                                <th className="text-right py-2 px-3 text-muted-foreground font-medium">Sessions</th>
                                <th className="text-right py-2 px-3 text-muted-foreground font-medium">Messages</th>
                                <th className="text-right py-2 px-3 text-muted-foreground font-medium">Voice</th>
                                <th className="text-right py-2 px-3 text-muted-foreground font-medium">Visitors</th>
                              </tr>
                            </thead>
                            <tbody>
                              {analytics.daily_breakdown.map((day) => (
                                <tr key={day.date} className="border-b border-muted/30">
                                  <td className="py-2 px-3">{new Date(day.date).toLocaleDateString()}</td>
                                  <td className="py-2 px-3 text-right">{day.sessions}</td>
                                  <td className="py-2 px-3 text-right">{day.messages}</td>
                                  <td className="py-2 px-3 text-right">{day.voice_calls}</td>
                                  <td className="py-2 px-3 text-right">{day.unique_visitors}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
    </div>
  )
}

export default function WidgetSettingsPage() {
  return (
    <DashboardLayout>
      <WidgetSettingsContent />
    </DashboardLayout>
  )
}
