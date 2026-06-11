/**
 * Phone Numbers — Add / Import dialog with three panels: manual registry
 * record, Telnyx import + availability lookup, and Africa's Talking import.
 */

import type { Dispatch, SetStateAction } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Button } from '@/components/atoms/button'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/atoms/dialog'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/atoms/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/atoms/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import { Textarea } from '@/components/atoms/textarea'
import type { PhoneNumberStatus, TelnyxAvailableNumber } from '@/types/phone'
import {
  TELNYX_COUNTRY_OPTIONS,
  type AfricasTalkingImportFormState,
  type ImportPanel,
  type ManualNumberFormState,
  type MutationLike,
  type TelnyxImportFormState,
  type TelnyxLookupFormState,
} from '../utils/phone-helpers'
import { ToggleField } from './PhoneBadges'

type AddNumberDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  addTab: ImportPanel
  setAddTab: (panel: ImportPanel) => void
  manualForm: ManualNumberFormState
  setManualForm: Dispatch<SetStateAction<ManualNumberFormState>>
  telnyxImportForm: TelnyxImportFormState
  setTelnyxImportForm: Dispatch<SetStateAction<TelnyxImportFormState>>
  telnyxLookupForm: TelnyxLookupFormState
  setTelnyxLookupForm: Dispatch<SetStateAction<TelnyxLookupFormState>>
  telnyxLookupResults: TelnyxAvailableNumber[]
  africasTalkingImportForm: AfricasTalkingImportFormState
  setAfricasTalkingImportForm: Dispatch<SetStateAction<AfricasTalkingImportFormState>>
  showAtImportAdvanced: boolean
  setShowAtImportAdvanced: Dispatch<SetStateAction<boolean>>
  createManualNumberMutation: MutationLike
  telnyxImportMutation: MutationLike
  telnyxLookupMutation: MutationLike
  africasTalkingImportMutation: MutationLike
}

export function AddNumberDialog({
  open,
  onOpenChange,
  addTab,
  setAddTab,
  manualForm,
  setManualForm,
  telnyxImportForm,
  setTelnyxImportForm,
  telnyxLookupForm,
  setTelnyxLookupForm,
  telnyxLookupResults,
  africasTalkingImportForm,
  setAfricasTalkingImportForm,
  showAtImportAdvanced,
  setShowAtImportAdvanced,
  createManualNumberMutation,
  telnyxImportMutation,
  telnyxLookupMutation,
  africasTalkingImportMutation,
}: AddNumberDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Add Phone Number</DialogTitle>
          <DialogDescription>
            Create a manual registry record or import a provider-owned phone number.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={addTab} onValueChange={(value) => setAddTab(value as ImportPanel)} className="space-y-5">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="registry">Manual Record</TabsTrigger>
            <TabsTrigger value="telnyx">Telnyx Import</TabsTrigger>
            <TabsTrigger value="africastalking">Africa&apos;s Talking</TabsTrigger>
          </TabsList>

          {/* Manual registry record */}
          <TabsContent value="registry" className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Create a canonical entry before provider binding exists — useful for staging, migrations, or numbers managed outside Telnyx or Africa&apos;s Talking.
            </p>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="manual-phone-number">E.164 Number</Label>
                <Input
                  id="manual-phone-number"
                  value={manualForm.e164_number}
                  onChange={(event) => setManualForm((current) => ({ ...current, e164_number: event.target.value }))}
                  placeholder="+2348012345678"
                />
              </div>
              <div>
                <Label htmlFor="manual-display-name">Display Name</Label>
                <Input
                  id="manual-display-name"
                  value={manualForm.display_name}
                  onChange={(event) => setManualForm((current) => ({ ...current, display_name: event.target.value }))}
                  placeholder="Lagos support line"
                />
              </div>
              <div>
                <Label>Ownership Mode</Label>
                <Select
                  value={manualForm.ownership_mode}
                  onValueChange={(value) =>
                    setManualForm((current) => ({
                      ...current,
                      ownership_mode: value as typeof current.ownership_mode,
                    }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="imported">Imported</SelectItem>
                    <SelectItem value="provider_managed">Provider Managed</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Status</Label>
                <Select
                  value={manualForm.status}
                  onValueChange={(value) =>
                    setManualForm((current) => ({ ...current, status: value as PhoneNumberStatus }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">Draft</SelectItem>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="suspended">Suspended</SelectItem>
                    <SelectItem value="archived">Archived</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="md:col-span-2">
                <Label htmlFor="manual-note">Registry Note</Label>
                <Textarea
                  id="manual-note"
                  value={manualForm.metadata_note}
                  onChange={(event) => setManualForm((current) => ({ ...current, metadata_note: event.target.value }))}
                  placeholder="Optional control-plane note for operators."
                />
              </div>
            </div>
            <div className="flex justify-end">
              <Button onClick={() => createManualNumberMutation.mutate()} disabled={createManualNumberMutation.isPending}>
                {createManualNumberMutation.isPending ? 'Creating…' : 'Create Registry Record'}
              </Button>
            </div>
          </TabsContent>

          {/* Telnyx import */}
          <TabsContent value="telnyx" className="space-y-5">
            {/* Import existing number */}
            <div className="space-y-4 rounded-2xl border border-sky-500/20 bg-sky-500/5 p-4">
              <div>
                <p className="text-sm font-medium">Import an existing Telnyx number</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Bring a purchased Telnyx number into the registry and hydrate voice-binding health from provider metadata.
                </p>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <Label htmlFor="telnyx-provider-id">Provider Resource ID</Label>
                  <Input
                    id="telnyx-provider-id"
                    value={telnyxImportForm.provider_resource_id}
                    onChange={(event) =>
                      setTelnyxImportForm((current) => ({ ...current, provider_resource_id: event.target.value }))
                    }
                    placeholder="1293384261075731499"
                  />
                </div>
                <div>
                  <Label htmlFor="telnyx-phone-number">Phone Number</Label>
                  <Input
                    id="telnyx-phone-number"
                    value={telnyxImportForm.phone_number}
                    onChange={(event) =>
                      setTelnyxImportForm((current) => ({ ...current, phone_number: event.target.value }))
                    }
                    placeholder="+2348012345678"
                  />
                </div>
                <div className="md:col-span-2">
                  <Label htmlFor="telnyx-display-name">Display Name</Label>
                  <Input
                    id="telnyx-display-name"
                    value={telnyxImportForm.display_name}
                    onChange={(event) =>
                      setTelnyxImportForm((current) => ({ ...current, display_name: event.target.value }))
                    }
                    placeholder="Nigeria sales line"
                  />
                </div>
              </div>
              <div className="rounded-xl border border-sky-500/20 bg-background/70 p-3 text-sm text-muted-foreground">
                Use a provider resource ID when you have it. Import by phone number only works after the number already exists in your Telnyx account.
              </div>
              <div className="flex justify-end">
                <Button onClick={() => telnyxImportMutation.mutate()} disabled={telnyxImportMutation.isPending}>
                  {telnyxImportMutation.isPending ? 'Importing…' : 'Import Telnyx Number'}
                </Button>
              </div>
            </div>

            {/* Availability lookup */}
            <div className="space-y-4 rounded-2xl border border-border/60 bg-background/60 p-4">
              <div>
                <p className="text-sm font-medium">Availability lookup</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Read-only inventory search for planning. Procurement remains manual.
                </p>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <Label>Country</Label>
                  <Select
                    value={telnyxLookupForm.country_code}
                    onValueChange={(value) =>
                      setTelnyxLookupForm((current) => ({ ...current, country_code: value }))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TELNYX_COUNTRY_OPTIONS.map((option) => (
                        <SelectItem key={option.code} value={option.code}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label htmlFor="telnyx-limit">Limit</Label>
                  <Input
                    id="telnyx-limit"
                    type="number"
                    min="1"
                    max="100"
                    value={telnyxLookupForm.limit}
                    onChange={(event) =>
                      setTelnyxLookupForm((current) => ({ ...current, limit: event.target.value }))
                    }
                  />
                </div>
                <div>
                  <Label htmlFor="telnyx-locality">Locality</Label>
                  <Input
                    id="telnyx-locality"
                    value={telnyxLookupForm.locality}
                    onChange={(event) =>
                      setTelnyxLookupForm((current) => ({ ...current, locality: event.target.value }))
                    }
                    placeholder="Lagos"
                  />
                </div>
                <div>
                  <Label htmlFor="telnyx-ndc">National Destination Code</Label>
                  <Input
                    id="telnyx-ndc"
                    value={telnyxLookupForm.national_destination_code}
                    onChange={(event) =>
                      setTelnyxLookupForm((current) => ({
                        ...current,
                        national_destination_code: event.target.value,
                      }))
                    }
                    placeholder="1"
                  />
                </div>
              </div>
              <div className="flex justify-end">
                <Button
                  variant="outline"
                  onClick={() => telnyxLookupMutation.mutate()}
                  disabled={telnyxLookupMutation.isPending}
                >
                  {telnyxLookupMutation.isPending ? 'Looking up…' : 'Look Up Numbers'}
                </Button>
              </div>
              {telnyxLookupResults.length > 0 && (
                <div className="rounded-xl border border-border/60">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Number</TableHead>
                        <TableHead>Location</TableHead>
                        <TableHead>Features</TableHead>
                        <TableHead>Monthly</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {telnyxLookupResults.map((number) => (
                        <TableRow key={number.phone_number}>
                          <TableCell className="font-medium">{number.phone_number}</TableCell>
                          <TableCell>{[number.locality, number.region].filter(Boolean).join(', ') || '—'}</TableCell>
                          <TableCell>{number.features.join(', ') || 'voice'}</TableCell>
                          <TableCell>
                            {number.monthly_cost ? `${number.monthly_cost} ${number.currency ?? ''}`.trim() : '—'}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          </TabsContent>

          {/* Africa's Talking import */}
          <TabsContent value="africastalking" className="space-y-4">
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-muted-foreground">
              Africa&apos;s Talking does not expose a provisioning API. Procurement, SIP forwarding, and IP allowlisting remain manual dashboard operations. This form records that manual state so routing health is visible in the registry.
            </div>

            {/* Primary fields */}
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="at-phone-number">Phone Number <span className="text-destructive">*</span></Label>
                <Input
                  id="at-phone-number"
                  value={africasTalkingImportForm.phone_number}
                  onChange={(event) =>
                    setAfricasTalkingImportForm((current) => ({ ...current, phone_number: event.target.value }))
                  }
                  placeholder="+2348012345678"
                />
              </div>
              <div>
                <Label htmlFor="at-display-name">Display Name</Label>
                <Input
                  id="at-display-name"
                  value={africasTalkingImportForm.display_name}
                  onChange={(event) =>
                    setAfricasTalkingImportForm((current) => ({ ...current, display_name: event.target.value }))
                  }
                  placeholder="Lagos support line"
                />
              </div>
              <div>
                <Label htmlFor="at-username">AT Username</Label>
                <Input
                  id="at-username"
                  value={africasTalkingImportForm.account_username}
                  onChange={(event) =>
                    setAfricasTalkingImportForm((current) => ({ ...current, account_username: event.target.value }))
                  }
                  placeholder="sandbox"
                />
              </div>
              <div>
                <Label htmlFor="at-credentials-reference">Credentials Reference</Label>
                <Input
                  id="at-credentials-reference"
                  value={africasTalkingImportForm.credentials_reference}
                  onChange={(event) =>
                    setAfricasTalkingImportForm((current) => ({
                      ...current,
                      credentials_reference: event.target.value,
                    }))
                  }
                  placeholder="ops/africastalking/main"
                />
              </div>
              <div>
                <Label htmlFor="at-voice-url">Voice Callback URL</Label>
                <Input
                  id="at-voice-url"
                  value={africasTalkingImportForm.voice_callback_url}
                  onChange={(event) =>
                    setAfricasTalkingImportForm((current) => ({
                      ...current,
                      voice_callback_url: event.target.value,
                    }))
                  }
                  placeholder="trunk:livekit.example.test"
                />
              </div>
              <div>
                <Label htmlFor="at-sip-target">SIP Trunk Target</Label>
                <Input
                  id="at-sip-target"
                  value={africasTalkingImportForm.sip_trunk_target}
                  onChange={(event) =>
                    setAfricasTalkingImportForm((current) => ({
                      ...current,
                      sip_trunk_target: event.target.value,
                    }))
                  }
                  placeholder="trunk:livekit.example.test"
                />
              </div>
            </div>

            {/* Advanced configuration — collapsible */}
            <div className="rounded-xl border border-border/60">
              <button
                type="button"
                onClick={() => setShowAtImportAdvanced((prev) => !prev)}
                className="flex w-full items-center justify-between rounded-xl px-4 py-3 text-left text-sm font-medium hover:bg-muted/30"
              >
                <span>Advanced configuration</span>
                {showAtImportAdvanced
                  ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  : <ChevronRight className="h-4 w-4 text-muted-foreground" />
                }
              </button>
              {showAtImportAdvanced && (
                <div className="space-y-4 border-t border-border/60 p-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <Label htmlFor="at-provider-id">Provider Resource ID</Label>
                      <Input
                        id="at-provider-id"
                        value={africasTalkingImportForm.provider_resource_id}
                        onChange={(event) =>
                          setAfricasTalkingImportForm((current) => ({
                            ...current,
                            provider_resource_id: event.target.value,
                          }))
                        }
                        placeholder="Optional override"
                      />
                    </div>
                    <div>
                      <Label htmlFor="at-events-url">Events Callback URL</Label>
                      <Input
                        id="at-events-url"
                        value={africasTalkingImportForm.events_callback_url}
                        onChange={(event) =>
                          setAfricasTalkingImportForm((current) => ({
                            ...current,
                            events_callback_url: event.target.value,
                          }))
                        }
                        placeholder="https://ops.example.test/africastalking/events"
                      />
                    </div>
                    <div>
                      <Label htmlFor="at-verified-at">Last Verified At</Label>
                      <Input
                        id="at-verified-at"
                        type="datetime-local"
                        value={africasTalkingImportForm.last_verified_at}
                        onChange={(event) =>
                          setAfricasTalkingImportForm((current) => ({
                            ...current,
                            last_verified_at: event.target.value,
                          }))
                        }
                      />
                    </div>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                    <ToggleField
                      label="SIP Auth Required"
                      checked={africasTalkingImportForm.sip_auth_required}
                      onCheckedChange={(checked) =>
                        setAfricasTalkingImportForm((current) => ({ ...current, sip_auth_required: checked }))
                      }
                    />
                    <ToggleField
                      label="IP Whitelist Confirmed"
                      checked={africasTalkingImportForm.ip_whitelist_confirmed}
                      onCheckedChange={(checked) =>
                        setAfricasTalkingImportForm((current) => ({
                          ...current,
                          ip_whitelist_confirmed: checked,
                        }))
                      }
                    />
                    <ToggleField
                      label="SIP Forwarding Confirmed"
                      checked={africasTalkingImportForm.sip_forwarding_confirmed}
                      onCheckedChange={(checked) =>
                        setAfricasTalkingImportForm((current) => ({
                          ...current,
                          sip_forwarding_confirmed: checked,
                        }))
                      }
                    />
                    <ToggleField
                      label="Provider Config Confirmed"
                      checked={africasTalkingImportForm.configuration_confirmed}
                      onCheckedChange={(checked) =>
                        setAfricasTalkingImportForm((current) => ({
                          ...current,
                          configuration_confirmed: checked,
                        }))
                      }
                    />
                  </div>
                  <div>
                    <Label htmlFor="at-notes">Operator Notes</Label>
                    <Textarea
                      id="at-notes"
                      value={africasTalkingImportForm.notes}
                      onChange={(event) =>
                        setAfricasTalkingImportForm((current) => ({ ...current, notes: event.target.value }))
                      }
                      placeholder="Document the current AT dashboard setup."
                    />
                  </div>
                </div>
              )}
            </div>

            <div className="flex justify-end">
              <Button
                onClick={() => africasTalkingImportMutation.mutate()}
                disabled={africasTalkingImportMutation.isPending}
              >
                {africasTalkingImportMutation.isPending ? 'Importing…' : "Import Africa's Talking Number"}
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
