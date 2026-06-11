/**
 * Phone Numbers — all registry mutations: manual create, number/route edits,
 * Telnyx import/lookup/sync, Africa's Talking import/sync, and reconciliation.
 *
 * Toasts, invalidations, and post-success form resets are kept byte-identical
 * to the original page implementation.
 */

import { startTransition, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { phoneService } from '@/api/services/phone.service'
import type {
  AfricasTalkingBindingSyncRequest,
  PhoneBindingReconciliationResponse,
  PhoneNumberDetail,
  TelnyxAvailableNumber,
} from '@/types/phone'
import {
  fromDateTimeLocalValue,
  type AfricasTalkingImportFormState,
  type ManualNumberFormState,
  type NumberEditFormState,
  type RouteFormState,
  type TelnyxImportFormState,
  type TelnyxLookupFormState,
} from '../utils/phone-helpers'

type UsePhoneRegistryMutationsArgs = {
  selectedDetail: PhoneNumberDetail | null
  setSelectedNumberId: Dispatch<SetStateAction<string | null>>
  setIsAddOpen: Dispatch<SetStateAction<boolean>>
  manualForm: ManualNumberFormState
  setManualForm: Dispatch<SetStateAction<ManualNumberFormState>>
  numberEditForm: NumberEditFormState
  routeForm: RouteFormState
  setRouteForm: Dispatch<SetStateAction<RouteFormState>>
  telnyxImportForm: TelnyxImportFormState
  setTelnyxImportForm: Dispatch<SetStateAction<TelnyxImportFormState>>
  telnyxLookupForm: TelnyxLookupFormState
  setTelnyxLookupResults: Dispatch<SetStateAction<TelnyxAvailableNumber[]>>
  africasTalkingImportForm: AfricasTalkingImportFormState
  setAfricasTalkingImportForm: Dispatch<SetStateAction<AfricasTalkingImportFormState>>
  setShowAtImportAdvanced: Dispatch<SetStateAction<boolean>>
}

export function usePhoneRegistryMutations({
  selectedDetail,
  setSelectedNumberId,
  setIsAddOpen,
  manualForm,
  setManualForm,
  numberEditForm,
  routeForm,
  setRouteForm,
  telnyxImportForm,
  setTelnyxImportForm,
  telnyxLookupForm,
  setTelnyxLookupResults,
  africasTalkingImportForm,
  setAfricasTalkingImportForm,
  setShowAtImportAdvanced,
}: UsePhoneRegistryMutationsArgs) {
  const queryClient = useQueryClient()

  const invalidatePhoneRegistry = async (phoneNumberId?: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['phone-registry-numbers'] }),
      queryClient.invalidateQueries({ queryKey: ['phone-registry-detail'] }),
      queryClient.invalidateQueries({ queryKey: ['phone-registry-detail', phoneNumberId] }),
      queryClient.invalidateQueries({ queryKey: ['phone-registry-audit'] }),
      queryClient.invalidateQueries({ queryKey: ['phone-registry-audit', phoneNumberId] }),
    ])
  }

  const createManualNumberMutation = useMutation({
    mutationFn: () =>
      phoneService.createPhoneNumber({
        e164_number: manualForm.e164_number,
        display_name: manualForm.display_name || null,
        ownership_mode: manualForm.ownership_mode,
        status: manualForm.status,
        metadata: manualForm.metadata_note ? { note: manualForm.metadata_note } : {},
      }),
    onSuccess: async (number) => {
      await invalidatePhoneRegistry(number.phone_number_id)
      toast.success('Phone record created')
      setManualForm({ e164_number: '', display_name: '', ownership_mode: 'imported', status: 'active', metadata_note: '' })
      setIsAddOpen(false)
      startTransition(() => setSelectedNumberId(number.phone_number_id))
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to create phone record'),
  })

  const updateNumberMutation = useMutation({
    mutationFn: () => {
      if (!selectedDetail) throw new Error('Select a number first')
      return phoneService.updatePhoneNumber(selectedDetail.number.phone_number_id, {
        display_name: numberEditForm.display_name || null,
        status: numberEditForm.status,
      })
    },
    onSuccess: async (number) => {
      await invalidatePhoneRegistry(number.phone_number_id)
      toast.success('Phone record updated')
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to update phone record'),
  })

  const createRouteMutation = useMutation({
    mutationFn: () => {
      if (!selectedDetail) throw new Error('Select a number first')
      if (!routeForm.agent_id) throw new Error('Select an agent')
      return phoneService.createPhoneRoute(selectedDetail.number.phone_number_id, {
        agent_id: routeForm.agent_id,
        priority: Number(routeForm.priority) || 100,
        enabled: routeForm.enabled,
        metadata: routeForm.purpose ? { purpose: routeForm.purpose } : {},
      })
    },
    onSuccess: async () => {
      if (!selectedDetail) return
      await invalidatePhoneRegistry(selectedDetail.number.phone_number_id)
      toast.success(routeForm.enabled ? 'Route saved as primary' : 'Route candidate added')
      setRouteForm((current) => ({ ...current, priority: '100', purpose: '', enabled: true }))
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to save route'),
  })

  const updateRouteMutation = useMutation({
    mutationFn: ({
      routeId,
      payload,
    }: {
      routeId: string
      payload: { enabled?: boolean; priority?: number; agent_id?: string; metadata?: Record<string, unknown> }
    }) => {
      if (!selectedDetail) throw new Error('Select a number first')
      return phoneService.updatePhoneRoute(selectedDetail.number.phone_number_id, routeId, payload)
    },
    onSuccess: async () => {
      if (!selectedDetail) return
      await invalidatePhoneRegistry(selectedDetail.number.phone_number_id)
      toast.success('Route updated')
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to update route'),
  })

  const telnyxImportMutation = useMutation({
    mutationFn: () =>
      phoneService.importTelnyxNumber({
        provider_resource_id: telnyxImportForm.provider_resource_id || null,
        phone_number: telnyxImportForm.phone_number || null,
        display_name: telnyxImportForm.display_name || null,
      }),
    onSuccess: async (result) => {
      await invalidatePhoneRegistry(result.number.phone_number_id)
      toast.success(result.created_number ? 'Telnyx number imported' : 'Telnyx binding refreshed')
      setTelnyxImportForm({ provider_resource_id: '', phone_number: '', display_name: '' })
      setIsAddOpen(false)
      startTransition(() => setSelectedNumberId(result.number.phone_number_id))
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to import Telnyx number'),
  })

  const telnyxLookupMutation = useMutation({
    mutationFn: () =>
      phoneService.lookupTelnyxAvailableNumbers({
        country_code: telnyxLookupForm.country_code,
        phone_number_type: telnyxLookupForm.phone_number_type,
        national_destination_code: telnyxLookupForm.national_destination_code || undefined,
        locality: telnyxLookupForm.locality || undefined,
        limit: Number(telnyxLookupForm.limit) || 10,
      }),
    onSuccess: (numbers) => {
      setTelnyxLookupResults(numbers)
      toast.success(`Loaded ${numbers.length} Telnyx candidate numbers`)
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to look up Telnyx numbers'),
  })

  const syncTelnyxBindingMutation = useMutation({
    mutationFn: (bindingId: string) => {
      if (!selectedDetail) throw new Error('Select a number first')
      return phoneService.syncTelnyxBinding(selectedDetail.number.phone_number_id, bindingId)
    },
    onSuccess: async (result) => {
      await invalidatePhoneRegistry(result.number.phone_number_id)
      toast.success('Telnyx binding synced')
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to sync Telnyx binding'),
  })

  const africasTalkingImportMutation = useMutation({
    mutationFn: () =>
      phoneService.importAfricasTalkingNumber({
        phone_number: africasTalkingImportForm.phone_number,
        provider_resource_id: africasTalkingImportForm.provider_resource_id || null,
        display_name: africasTalkingImportForm.display_name || null,
        account_username: africasTalkingImportForm.account_username || null,
        voice_callback_url: africasTalkingImportForm.voice_callback_url || null,
        events_callback_url: africasTalkingImportForm.events_callback_url || null,
        sip_trunk_target: africasTalkingImportForm.sip_trunk_target || null,
        sip_auth_required: africasTalkingImportForm.sip_auth_required,
        credentials_reference: africasTalkingImportForm.credentials_reference || null,
        ip_whitelist_confirmed: africasTalkingImportForm.ip_whitelist_confirmed,
        sip_forwarding_confirmed: africasTalkingImportForm.sip_forwarding_confirmed,
        configuration_confirmed: africasTalkingImportForm.configuration_confirmed,
        last_verified_at: fromDateTimeLocalValue(africasTalkingImportForm.last_verified_at),
        notes: africasTalkingImportForm.notes || null,
      }),
    onSuccess: async (result) => {
      await invalidatePhoneRegistry(result.number.phone_number_id)
      toast.success(result.created_number ? "Africa's Talking number imported" : "Africa's Talking binding refreshed")
      setAfricasTalkingImportForm({
        phone_number: '', provider_resource_id: '', display_name: '', account_username: '',
        voice_callback_url: '', events_callback_url: '', sip_trunk_target: '', sip_auth_required: true,
        credentials_reference: '', ip_whitelist_confirmed: false, sip_forwarding_confirmed: false,
        configuration_confirmed: false, last_verified_at: '', notes: '',
      })
      setShowAtImportAdvanced(false)
      setIsAddOpen(false)
      startTransition(() => setSelectedNumberId(result.number.phone_number_id))
    },
    onError: (error: Error) => toast.error(error.message || "Unable to import Africa's Talking number"),
  })

  const syncAfricasTalkingBindingMutation = useMutation({
    mutationFn: ({ bindingId, payload }: { bindingId: string; payload: AfricasTalkingBindingSyncRequest }) => {
      if (!selectedDetail) throw new Error('Select a number first')
      return phoneService.syncAfricasTalkingBinding(selectedDetail.number.phone_number_id, bindingId, payload)
    },
    onSuccess: async (result) => {
      await invalidatePhoneRegistry(result.number.phone_number_id)
      toast.success("Africa's Talking state saved")
    },
    onError: (error: Error) => toast.error(error.message || "Unable to save Africa's Talking state"),
  })

  const reconcileNumberMutation = useMutation({
    mutationFn: () => {
      if (!selectedDetail) throw new Error('Select a number first')
      return phoneService.reconcilePhoneBindings({
        phone_number_id: selectedDetail.number.phone_number_id,
        limit: 25,
      })
    },
    onSuccess: async (result: PhoneBindingReconciliationResponse) => {
      if (!selectedDetail) return
      await invalidatePhoneRegistry(selectedDetail.number.phone_number_id)
      if (result.failed_count > 0) {
        toast.error(`Reconciliation finished with ${result.failed_count} failure${result.failed_count === 1 ? '' : 's'}`)
        return
      }
      toast.success(
        result.changed_count > 0
          ? `Reconciled ${result.processed_count} binding${result.processed_count === 1 ? '' : 's'}; ${result.changed_count} changed`
          : `Reconciled ${result.processed_count} binding${result.processed_count === 1 ? '' : 's'} with no drift`,
      )
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to reconcile bindings'),
  })

  return {
    createManualNumberMutation,
    updateNumberMutation,
    createRouteMutation,
    updateRouteMutation,
    telnyxImportMutation,
    telnyxLookupMutation,
    syncTelnyxBindingMutation,
    africasTalkingImportMutation,
    syncAfricasTalkingBindingMutation,
    reconcileNumberMutation,
  }
}
