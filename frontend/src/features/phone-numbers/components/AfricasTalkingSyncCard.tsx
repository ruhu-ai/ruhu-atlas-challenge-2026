/**
 * Phone Numbers — Africa's Talking manual binding state editor.
 *
 * Keeps the callback URL, SIP target, and operator confirmations aligned
 * with the provider dashboard for a single binding.
 */

import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import type { AfricasTalkingBindingSyncRequest, PhoneNumberBinding } from '@/types/phone'
import {
  fromDateTimeLocalValue,
  getAfricasTalkingState,
  humanizeManualItem,
  toDateTimeLocalValue,
} from '../utils/phone-helpers'
import { HealthBadge, ToggleField, VerificationBadge } from './PhoneBadges'

export function AfricasTalkingSyncCard({
  phoneNumberId,
  binding,
  onSubmit,
  isPending,
}: {
  phoneNumberId: string
  binding: PhoneNumberBinding
  onSubmit: (bindingId: string, payload: AfricasTalkingBindingSyncRequest) => void
  isPending: boolean
}) {
  const state = getAfricasTalkingState(binding)
  // Expand advanced section automatically when there are pending manual requirements
  const [showAdvanced, setShowAdvanced] = useState(() => (state?.manual_requirements.length ?? 0) > 0)
  const [formState, setFormState] = useState<AfricasTalkingBindingSyncRequest>({
    provider_resource_id: state?.provider_resource_id ?? binding.provider_resource_id ?? '',
    account_username: state?.account_username ?? '',
    voice_callback_url: state?.voice_callback_url ?? '',
    events_callback_url: state?.events_callback_url ?? '',
    sip_trunk_target: state?.sip_trunk_target ?? '',
    sip_auth_required: state?.sip_auth_required ?? true,
    credentials_reference: state?.credentials_reference ?? '',
    ip_whitelist_confirmed: state?.ip_whitelist_confirmed ?? false,
    sip_forwarding_confirmed: state?.sip_forwarding_confirmed ?? false,
    configuration_confirmed: state?.configuration_confirmed ?? false,
    last_verified_at: state?.last_verified_at ?? '',
    notes: state?.notes ?? '',
  })

  // Re-initialise form when server data for this binding changes.
  // binding is a React Query result — its reference only changes when the server
  // returns new data, so depending on the full object is safe and correct here.
  useEffect(() => {
    const fresh = getAfricasTalkingState(binding)
    setFormState({
      provider_resource_id: fresh?.provider_resource_id ?? binding.provider_resource_id ?? '',
      account_username: fresh?.account_username ?? '',
      voice_callback_url: fresh?.voice_callback_url ?? '',
      events_callback_url: fresh?.events_callback_url ?? '',
      sip_trunk_target: fresh?.sip_trunk_target ?? '',
      sip_auth_required: fresh?.sip_auth_required ?? true,
      credentials_reference: fresh?.credentials_reference ?? '',
      ip_whitelist_confirmed: fresh?.ip_whitelist_confirmed ?? false,
      sip_forwarding_confirmed: fresh?.sip_forwarding_confirmed ?? false,
      configuration_confirmed: fresh?.configuration_confirmed ?? false,
      last_verified_at: fresh?.last_verified_at ?? '',
      notes: fresh?.notes ?? '',
    })
    setShowAdvanced((fresh?.manual_requirements.length ?? 0) > 0)
  }, [binding])

  return (
    <div className="space-y-4 rounded-2xl border border-orange-500/20 bg-orange-500/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Africa&apos;s Talking Manual State</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Keep the callback URL, SIP target, and operator confirmations aligned with the provider dashboard.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <VerificationBadge status={binding.verification_status} />
          <HealthBadge status={binding.health_status} />
        </div>
      </div>

      {/* Primary fields — always visible */}
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label htmlFor={`${phoneNumberId}-${binding.binding_id}-username`}>AT Username</Label>
          <Input
            id={`${phoneNumberId}-${binding.binding_id}-username`}
            value={formState.account_username ?? ''}
            onChange={(event) => setFormState((current) => ({ ...current, account_username: event.target.value }))}
            placeholder="sandbox"
          />
        </div>
        <div>
          <Label htmlFor={`${phoneNumberId}-${binding.binding_id}-credentials`}>Credentials Reference</Label>
          <Input
            id={`${phoneNumberId}-${binding.binding_id}-credentials`}
            value={formState.credentials_reference ?? ''}
            onChange={(event) =>
              setFormState((current) => ({ ...current, credentials_reference: event.target.value }))
            }
            placeholder="ops/africastalking/main"
          />
        </div>
        <div>
          <Label htmlFor={`${phoneNumberId}-${binding.binding_id}-voice-url`}>Voice Callback URL</Label>
          <Input
            id={`${phoneNumberId}-${binding.binding_id}-voice-url`}
            value={formState.voice_callback_url ?? ''}
            onChange={(event) =>
              setFormState((current) => ({ ...current, voice_callback_url: event.target.value }))
            }
            placeholder="trunk:livekit.example.test"
          />
        </div>
        <div>
          <Label htmlFor={`${phoneNumberId}-${binding.binding_id}-sip-target`}>SIP Trunk Target</Label>
          <Input
            id={`${phoneNumberId}-${binding.binding_id}-sip-target`}
            value={formState.sip_trunk_target ?? ''}
            onChange={(event) =>
              setFormState((current) => ({ ...current, sip_trunk_target: event.target.value }))
            }
            placeholder="trunk:livekit.example.test"
          />
        </div>
      </div>

      {/* Advanced configuration — collapsible */}
      <div className="rounded-xl border border-orange-500/20">
        <button
          type="button"
          onClick={() => setShowAdvanced((prev) => !prev)}
          className="flex w-full items-center justify-between rounded-xl px-4 py-3 text-left text-sm font-medium hover:bg-orange-500/5"
        >
          <span>Advanced configuration</span>
          {showAdvanced
            ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
            : <ChevronRight className="h-4 w-4 text-muted-foreground" />
          }
        </button>
        {showAdvanced && (
          <div className="space-y-4 border-t border-orange-500/20 p-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <Label htmlFor={`${phoneNumberId}-${binding.binding_id}-events-url`}>Events Callback URL</Label>
                <Input
                  id={`${phoneNumberId}-${binding.binding_id}-events-url`}
                  value={formState.events_callback_url ?? ''}
                  onChange={(event) =>
                    setFormState((current) => ({ ...current, events_callback_url: event.target.value }))
                  }
                  placeholder="https://ops.example.test/africastalking/events"
                />
              </div>
              <div>
                <Label htmlFor={`${phoneNumberId}-${binding.binding_id}-verified-at`}>Last Verified At</Label>
                <Input
                  id={`${phoneNumberId}-${binding.binding_id}-verified-at`}
                  type="datetime-local"
                  value={toDateTimeLocalValue(formState.last_verified_at)}
                  onChange={(event) =>
                    setFormState((current) => ({ ...current, last_verified_at: fromDateTimeLocalValue(event.target.value) }))
                  }
                />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <ToggleField
                label="SIP Auth Required"
                checked={formState.sip_auth_required ?? true}
                onCheckedChange={(checked) => setFormState((current) => ({ ...current, sip_auth_required: checked }))}
              />
              <ToggleField
                label="IP Whitelist Confirmed"
                checked={formState.ip_whitelist_confirmed ?? false}
                onCheckedChange={(checked) =>
                  setFormState((current) => ({ ...current, ip_whitelist_confirmed: checked }))
                }
              />
              <ToggleField
                label="SIP Forwarding Confirmed"
                checked={formState.sip_forwarding_confirmed ?? false}
                onCheckedChange={(checked) =>
                  setFormState((current) => ({ ...current, sip_forwarding_confirmed: checked }))
                }
              />
              <ToggleField
                label="Provider Config Confirmed"
                checked={formState.configuration_confirmed ?? false}
                onCheckedChange={(checked) =>
                  setFormState((current) => ({ ...current, configuration_confirmed: checked }))
                }
              />
            </div>
            <div>
              <Label htmlFor={`${phoneNumberId}-${binding.binding_id}-notes`}>Operator Notes</Label>
              <Textarea
                id={`${phoneNumberId}-${binding.binding_id}-notes`}
                value={formState.notes ?? ''}
                onChange={(event) => setFormState((current) => ({ ...current, notes: event.target.value }))}
                placeholder="Document what was verified in the AT dashboard."
              />
            </div>
          </div>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-xl border border-border/60 bg-background/70 p-3">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Manual Requirements</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {state?.manual_requirements.length ? (
              state.manual_requirements.map((item) => (
                <Badge key={item} variant="outline" className="border-amber-500/40 text-amber-300">
                  {humanizeManualItem(item)}
                </Badge>
              ))
            ) : (
              <Badge variant="outline" className="border-emerald-500/40 text-emerald-300">
                Fully confirmed
              </Badge>
            )}
          </div>
        </div>
        <div className="rounded-xl border border-border/60 bg-background/70 p-3">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Recommended Actions</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {state?.recommended_actions.length ? (
              state.recommended_actions.map((item) => (
                <Badge key={item} variant="outline" className="border-sky-500/40 text-sky-300">
                  {humanizeManualItem(item)}
                </Badge>
              ))
            ) : (
              <Badge variant="outline" className="border-emerald-500/40 text-emerald-300">
                No follow-up pending
              </Badge>
            )}
          </div>
        </div>
      </div>

      <div className="flex justify-end">
        <Button onClick={() => onSubmit(binding.binding_id, formState)} disabled={isPending}>
          {isPending ? 'Saving AT state…' : 'Save AT State'}
        </Button>
      </div>
    </div>
  )
}
