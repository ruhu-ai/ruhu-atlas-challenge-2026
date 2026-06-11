/**
 * Agent Channels Tab
 *
 * Allows customers to configure communication channels (WhatsApp, SMS, etc.)
 * for their agents. Each channel links the agent to a specific phone number
 * or endpoint for receiving messages.
 */

import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { MessageSquare, Phone, Plus, Trash2, Check, X, Loader2, ExternalLink } from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/atoms/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { channelsService, type ChannelConfig, type WhatsAppConfigCreate, type OAuthPhoneNumber } from '@/api/services/channels.service'
import { toast } from 'sonner'

interface AgentChannelsTabProps {
  agentId: string
  agentName: string
  agentType: 'chat' | 'voice' | 'multimodal'
}

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  whatsapp: <MessageSquare className="h-4 w-4 text-green-500" />,
  sms: <Phone className="h-4 w-4 text-blue-500" />,
  voice: <Phone className="h-4 w-4 text-purple-500" />,
}

const CHANNEL_LABELS: Record<string, string> = {
  whatsapp: 'WhatsApp',
  sms: 'SMS',
  voice: 'Voice',
}

export function AgentChannelsTab({ agentId, agentName, agentType }: AgentChannelsTabProps) {
  const queryClient = useQueryClient()
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false)
  const [selectedChannelType, setSelectedChannelType] = useState<string>('')

  // Fetch channels for this agent
  const { data: channels = [], isLoading } = useQuery({
    queryKey: ['agent-channels', agentId],
    queryFn: () => channelsService.getAgentChannels(agentId),
    enabled: !!agentId,
  })

  // Get available channels based on agent type
  const availableChannels = channelsService.getAvailableChannels(agentType)

  // Filter out already configured channels
  const unconfiguredChannels = availableChannels.filter(
    (channelType) => !channels.some((c) => c.channel_type === channelType)
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">Connected Channels</h3>
          <p className="text-xs text-muted-foreground">
            Configure which channels this agent can receive messages from
          </p>
        </div>
        {unconfiguredChannels.length > 0 && (
          <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
            <DialogTrigger asChild>
              <Button size="sm" variant="outline" className="gap-2">
                <Plus className="h-4 w-4" />
                Add Channel
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add Channel</DialogTitle>
                <DialogDescription>
                  Connect a communication channel to {agentName}
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <Label>Channel Type</Label>
                  <Select value={selectedChannelType} onValueChange={setSelectedChannelType}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select a channel type" />
                    </SelectTrigger>
                    <SelectContent>
                      {unconfiguredChannels.map((channelType) => (
                        <SelectItem key={channelType} value={channelType}>
                          <div className="flex items-center gap-2">
                            {CHANNEL_ICONS[channelType]}
                            {CHANNEL_LABELS[channelType]}
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {selectedChannelType === 'whatsapp' && (
                  <WhatsAppEmbeddedSignup
                    agentId={agentId}
                    onSuccess={() => {
                      setIsAddDialogOpen(false)
                      setSelectedChannelType('')
                      queryClient.invalidateQueries({ queryKey: ['agent-channels', agentId] })
                    }}
                    onCancel={() => {
                      setIsAddDialogOpen(false)
                      setSelectedChannelType('')
                    }}
                  />
                )}

                {selectedChannelType && selectedChannelType !== 'whatsapp' && (
                  <div className="rounded-md border border-border bg-muted/40 p-4">
                    <p className="text-sm text-muted-foreground">
                      {CHANNEL_LABELS[selectedChannelType]} channel configuration coming soon.
                    </p>
                  </div>
                )}
              </div>
            </DialogContent>
          </Dialog>
        )}
      </div>

      {/* Channel List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : channels.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-8 text-center">
            <MessageSquare className="h-8 w-8 text-muted-foreground mb-3" />
            <p className="text-sm text-muted-foreground">
              No channels configured yet
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Add a channel to start receiving messages
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {channels.map((channel) => (
            <ChannelCard key={channel.id} channel={channel} agentId={agentId} />
          ))}
        </div>
      )}

      {/* Info box for supported channels */}
      <div className="rounded-md border border-primary/20 bg-primary/5 p-3 text-xs">
        <p className="font-medium text-primary mb-1">Supported Channels:</p>
        <div className="flex flex-wrap gap-2 mt-2">
          {availableChannels.map((channelType) => (
            <span
              key={channelType}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary"
            >
              {CHANNEL_ICONS[channelType]}
              {CHANNEL_LABELS[channelType]}
            </span>
          ))}
        </div>
        <p className="mt-2 text-muted-foreground">
          Based on your agent type ({agentType}), these channels are available for configuration.
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// WhatsApp Embedded Signup flow
// ---------------------------------------------------------------------------

type OAuthStep = 'idle' | 'loading' | 'select' | 'success' | 'error'

function WhatsAppEmbeddedSignup({
  agentId,
  onSuccess,
  onCancel,
}: {
  agentId: string
  onSuccess: () => void
  onCancel: () => void
}) {
  const [step, setStep] = useState<OAuthStep>('idle')
  const [errorMsg, setErrorMsg] = useState('')
  const [sessionKey, setSessionKey] = useState('')
  const [phoneNumbers, setPhoneNumbers] = useState<OAuthPhoneNumber[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [connectedConfig, setConnectedConfig] = useState<{ display_phone_number: string; verified_name: string; verify_token: string } | null>(null)
  const isHttp = window.location.protocol !== 'https:'
  const [showManual, setShowManual] = useState(isHttp)

  // Inject Meta JS SDK once on mount
  useEffect(() => {
    if (document.getElementById('facebook-jssdk')) return
    const script = document.createElement('script')
    script.id = 'facebook-jssdk'
    script.src = 'https://connect.facebook.net/en_US/sdk.js'
    script.async = true
    script.defer = true
    document.body.appendChild(script)
  }, [])

  // Async handler extracted so the FB.login callback stays synchronous
  const processOAuthCode = async (code: string) => {
    try {
      const result = await channelsService.exchangeOAuthCode(code, agentId)
      setSessionKey(result.session_key)
      setPhoneNumbers(result.phone_numbers)
      if (result.phone_numbers.length === 1) {
        setSelectedId(result.phone_numbers[0].phone_number_id)
      }
      setStep('select')
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to fetch phone numbers from Meta')
      setStep('error')
    }
  }

  const handleConnect = () => {
    const FB = (window as any).FB
    if (!FB) {
      setErrorMsg('Meta SDK not loaded yet — please try again in a moment.')
      setStep('error')
      return
    }

    setStep('loading')
    setErrorMsg('')

    // FB.login callback must be synchronous — kick off async work via .then chain
    FB.login(
      (response: any) => {
        if (response.authResponse?.code) {
          processOAuthCode(response.authResponse.code)
        } else {
          setStep('idle')
        }
      },
      {
        config_id: import.meta.env.VITE_META_APP_ID,
        response_type: 'code',
        override_default_response_type: true,
        extras: { setup: {}, featureType: '', sessionInfoVersion: '3' },
      }
    )
  }

  const handleSelect = async () => {
    if (!selectedId) return
    setStep('loading')
    try {
      const config = await channelsService.selectPhoneNumber({ session_key: sessionKey, agent_id: agentId, phone_number_id: selectedId })
      setConnectedConfig({
        display_phone_number: config.display_phone_number || config.phone_number_id,
        verified_name: config.verified_name || '',
        verify_token: config.verify_token,
      })
      setStep('success')
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to save WhatsApp configuration')
      setStep('error')
    }
  }

  // ── Success state ──────────────────────────────────────────────────────────
  if (step === 'success' && connectedConfig) {
    return (
      <div className="space-y-4">
        <div className="rounded-md border border-green-500/30 bg-green-500/10 p-4 space-y-1">
          <p className="text-sm font-medium text-green-400">WhatsApp connected</p>
          <p className="text-xs text-muted-foreground">Phone: <span className="text-foreground">{connectedConfig.display_phone_number}</span></p>
          {connectedConfig.verified_name && (
            <p className="text-xs text-muted-foreground">Name: <span className="text-foreground">{connectedConfig.verified_name}</span></p>
          )}
        </div>

        <div className="rounded-md border border-border bg-muted/40 p-3 space-y-2">
          <p className="text-xs font-medium text-foreground">Final step — configure your Meta webhook</p>
          <p className="text-xs text-muted-foreground">Callback URL:</p>
          <code className="text-xs text-foreground block break-all">{`${window.location.origin}/webhooks/whatsapp`}</code>
          <p className="text-xs text-muted-foreground mt-1">Verify token:</p>
          <code className="text-xs text-foreground block break-all">{connectedConfig.verify_token}</code>
        </div>

        <DialogFooter>
          <Button onClick={onSuccess}>Done</Button>
        </DialogFooter>
      </div>
    )
  }

  // ── Phone number selection ─────────────────────────────────────────────────
  if (step === 'select') {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Select the WhatsApp number to connect to this agent:
        </p>

        <div className="space-y-2">
          {phoneNumbers.map((pn) => (
            <button
              key={pn.phone_number_id}
              type="button"
              onClick={() => setSelectedId(pn.phone_number_id)}
              className={`w-full text-left rounded-md border p-3 transition-colors ${
                selectedId === pn.phone_number_id
                  ? 'border-primary bg-primary/10'
                  : 'border-border hover:border-primary/50'
              }`}
            >
              <p className="text-sm font-medium">{pn.display_phone_number || pn.phone_number_id}</p>
              <p className="text-xs text-muted-foreground">{pn.verified_name} · {pn.business_name}</p>
              {pn.quality_rating && pn.quality_rating !== 'UNKNOWN' && (
                <p className="text-xs text-muted-foreground">Quality: {pn.quality_rating}</p>
              )}
            </button>
          ))}
        </div>

        <DialogFooter className="gap-2">
          <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
          <Button onClick={handleSelect} disabled={!selectedId}>
            Connect this number
          </Button>
        </DialogFooter>
      </div>
    )
  }

  // ── Error state ────────────────────────────────────────────────────────────
  if (step === 'error') {
    return (
      <div className="space-y-4">
        <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3">
          <p className="text-xs text-red-400">{errorMsg}</p>
        </div>
        <DialogFooter className="gap-2">
          <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
          <Button onClick={() => setStep('idle')}>Try again</Button>
        </DialogFooter>
      </div>
    )
  }

  // ── Idle / manual fallback ─────────────────────────────────────────────────
  return (
    <div className="space-y-4">
      {isHttp ? (
        <div className="rounded-md border border-border bg-muted/40 p-3">
          <p className="text-xs font-medium text-foreground mb-1">HTTPS required for Meta login</p>
          <p className="text-xs text-muted-foreground">
            Meta's OAuth popup only works over HTTPS. Use the manual form below, or open the app via an HTTPS tunnel (e.g. ngrok) to use one-click connect.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Connect your WhatsApp Business account in one click via Meta's secure login.
          </p>
          <Button
            type="button"
            className="w-full gap-2 bg-[#1877F2] hover:bg-[#166FE5] text-white"
            onClick={handleConnect}
            disabled={step === 'loading'}
          >
            {step === 'loading' ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Connecting…</>
            ) : (
              <><MessageSquare className="h-4 w-4" /> Connect with Meta</>
            )}
          </Button>
        </div>
      )}

      <div className={isHttp ? undefined : 'border-t border-border pt-3'}>
        {!isHttp && (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setShowManual((v) => !v)}
          >
            {showManual ? '▾' : '▸'} Advanced: manual setup
          </button>
        )}

        {showManual && (
          <ManualWhatsAppConfigForm agentId={agentId} onSuccess={onSuccess} onCancel={onCancel} />
        )}
      </div>

      {!showManual && (
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
        </DialogFooter>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Manual / advanced configuration form (kept as fallback)
// ---------------------------------------------------------------------------

function ManualWhatsAppConfigForm({
  agentId,
  onSuccess,
  onCancel,
}: {
  agentId: string
  onSuccess: () => void
  onCancel: () => void
}) {
  const [formData, setFormData] = useState({
    phone_number_id: '',
    access_token: '',
    app_secret: '',
  })
  const [isTesting, setIsTesting] = useState(false)

  const createMutation = useMutation({
    mutationFn: (config: WhatsAppConfigCreate) => channelsService.createWhatsAppConfig(config),
    onSuccess: () => { onSuccess() },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMutation.mutate({ agent_id: agentId, ...formData })
  }

  const handleTest = async () => {
    setIsTesting(true)
    try {
      await channelsService.createWhatsAppConfig({ agent_id: agentId, ...formData })
      const result = await channelsService.testWhatsAppConnection()
      if (result.status === 'success') {
        toast.success('Connection successful!')
      } else {
        toast.error(`Connection failed: ${result.message}`)
      }
    } catch (error: any) {
      toast.error(error.message || 'Failed to test connection')
    } finally {
      setIsTesting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 mt-3">
      <div className="space-y-2">
        <Label htmlFor="phone_number_id">Phone Number ID</Label>
        <Input
          id="phone_number_id"
          value={formData.phone_number_id}
          onChange={(e) => setFormData({ ...formData, phone_number_id: e.target.value })}
          placeholder="123456789012345"
          required
        />
        <p className="text-xs text-muted-foreground">
          Find this in Meta Business Suite under WhatsApp &gt; Phone Numbers
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="access_token">Access Token</Label>
        <Input
          id="access_token"
          type="password"
          value={formData.access_token}
          onChange={(e) => setFormData({ ...formData, access_token: e.target.value })}
          placeholder="EAABsbCS1..."
          required
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="app_secret">App Secret</Label>
        <Input
          id="app_secret"
          type="password"
          value={formData.app_secret}
          onChange={(e) => setFormData({ ...formData, app_secret: e.target.value })}
          placeholder="a1b2c3d4e5f6..."
          required
        />
      </div>

      <div className="rounded-md border border-border bg-muted/40 p-3">
        <p className="text-xs font-medium text-foreground mb-1">Webhook URL</p>
        <code className="text-xs text-muted-foreground block mt-1">
          {`${window.location.origin}/webhooks/whatsapp`}
        </code>
      </div>

      <DialogFooter className="gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
        <Button
          type="button"
          variant="outline"
          onClick={handleTest}
          disabled={isTesting || !formData.phone_number_id || !formData.access_token}
        >
          {isTesting ? (
            <><Loader2 className="h-4 w-4 mr-2 animate-spin" />Testing…</>
          ) : 'Test Connection'}
        </Button>
        <Button type="submit" disabled={createMutation.isPending}>
          {createMutation.isPending ? (
            <><Loader2 className="h-4 w-4 mr-2 animate-spin" />Saving…</>
          ) : 'Save'}
        </Button>
      </DialogFooter>
    </form>
  )
}

// Individual channel card
function ChannelCard({ channel, agentId }: { channel: ChannelConfig; agentId: string }) {
  const queryClient = useQueryClient()

  return (
    <Card className="bg-card/50">
      <CardContent className="flex items-center justify-between p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-muted">
            {CHANNEL_ICONS[channel.channel_type]}
          </div>
          <div>
            <p className="text-sm font-medium">{CHANNEL_LABELS[channel.channel_type]}</p>
            {channel.phone_number_id && (
              <p className="text-xs text-muted-foreground">
                ID: {channel.phone_number_id}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {channel.is_enabled ? (
            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-green-500/10 text-green-400 text-xs">
              <Check className="h-3 w-3" />
              Active
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-gray-500/10 text-gray-400 text-xs">
              <X className="h-3 w-3" />
              Disabled
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export default AgentChannelsTab
