import { apiClient } from '../client'
import type {
  AfricasTalkingBindingSyncRequest,
  AfricasTalkingBindingSyncResponse,
  AfricasTalkingPhoneNumberImportRequest,
  PhoneAuditEvent,
  PhoneBindingReconciliationRequest,
  PhoneBindingReconciliationResponse,
  PhoneNumberCreateRequest,
  PhoneNumberDetail,
  PhoneNumberRoute,
  PhoneNumberRouteCreateRequest,
  PhoneNumberRouteUpdateRequest,
  PhoneNumberUpdateRequest,
  PhoneRegistryNumber,
  TelnyxAvailableNumber,
  TelnyxBindingSyncResponse,
  TelnyxPhoneNumberImportRequest,
} from '@/types/phone'

const PHONE_NUMBERS_PATH = '/phone-numbers'
const PHONE_PROVIDERS_PATH = '/phone-providers'

class PhoneRegistryService {
  async listPhoneNumbers(params?: {
    status?: string
    limit?: number
    offset?: number
  }): Promise<PhoneRegistryNumber[]> {
    return apiClient.get<PhoneRegistryNumber[]>(PHONE_NUMBERS_PATH, { params })
  }

  async getPhoneNumberDetail(phoneNumberId: string): Promise<PhoneNumberDetail> {
    return apiClient.get<PhoneNumberDetail>(`${PHONE_NUMBERS_PATH}/${phoneNumberId}`)
  }

  async createPhoneNumber(payload: PhoneNumberCreateRequest): Promise<PhoneRegistryNumber> {
    return apiClient.post<PhoneRegistryNumber>(PHONE_NUMBERS_PATH, payload)
  }

  async updatePhoneNumber(
    phoneNumberId: string,
    payload: PhoneNumberUpdateRequest,
  ): Promise<PhoneRegistryNumber> {
    return apiClient.patch<PhoneRegistryNumber>(`${PHONE_NUMBERS_PATH}/${phoneNumberId}`, payload)
  }

  async createPhoneRoute(
    phoneNumberId: string,
    payload: PhoneNumberRouteCreateRequest,
  ): Promise<PhoneNumberRoute> {
    return apiClient.post<PhoneNumberRoute>(`${PHONE_NUMBERS_PATH}/${phoneNumberId}/routes`, payload)
  }

  async updatePhoneRoute(
    phoneNumberId: string,
    routeId: string,
    payload: PhoneNumberRouteUpdateRequest,
  ): Promise<PhoneNumberRoute> {
    return apiClient.patch<PhoneNumberRoute>(`${PHONE_NUMBERS_PATH}/${phoneNumberId}/routes/${routeId}`, payload)
  }

  async listPhoneAuditEvents(params: {
    phone_number_id?: string
    resource_type?: string
    resource_id?: string
    limit?: number
  }): Promise<PhoneAuditEvent[]> {
    return apiClient.get<PhoneAuditEvent[]>(`${PHONE_NUMBERS_PATH}/audit`, {
      params,
    })
  }

  async reconcilePhoneBindings(
    payload: PhoneBindingReconciliationRequest,
  ): Promise<PhoneBindingReconciliationResponse> {
    return apiClient.post<PhoneBindingReconciliationResponse>(`${PHONE_NUMBERS_PATH}/reconcile`, payload)
  }

  async importTelnyxNumber(payload: TelnyxPhoneNumberImportRequest): Promise<TelnyxBindingSyncResponse> {
    return apiClient.post<TelnyxBindingSyncResponse>(`${PHONE_PROVIDERS_PATH}/telnyx/import`, payload)
  }

  async lookupTelnyxAvailableNumbers(params: {
    country_code: string
    phone_number_type?: string
    national_destination_code?: string
    locality?: string
    limit?: number
  }): Promise<TelnyxAvailableNumber[]> {
    return apiClient.get<TelnyxAvailableNumber[]>(`${PHONE_PROVIDERS_PATH}/telnyx/available-numbers`, {
      params,
    })
  }

  async syncTelnyxBinding(
    phoneNumberId: string,
    bindingId: string,
  ): Promise<TelnyxBindingSyncResponse> {
    return apiClient.post<TelnyxBindingSyncResponse>(
      `${PHONE_NUMBERS_PATH}/${phoneNumberId}/bindings/${bindingId}/providers/telnyx/sync`,
    )
  }

  async importAfricasTalkingNumber(
    payload: AfricasTalkingPhoneNumberImportRequest,
  ): Promise<AfricasTalkingBindingSyncResponse> {
    return apiClient.post<AfricasTalkingBindingSyncResponse>(
      `${PHONE_PROVIDERS_PATH}/africastalking/import`,
      payload,
    )
  }

  async syncAfricasTalkingBinding(
    phoneNumberId: string,
    bindingId: string,
    payload: AfricasTalkingBindingSyncRequest,
  ): Promise<AfricasTalkingBindingSyncResponse> {
    return apiClient.post<AfricasTalkingBindingSyncResponse>(
      `${PHONE_NUMBERS_PATH}/${phoneNumberId}/bindings/${bindingId}/providers/africastalking/sync`,
      payload,
    )
  }

  async validateAfricasTalkingCredentials(payload: {
    username: string
    api_key: string
  }): Promise<{
    valid: boolean
    username: string
    account_type: string | null
    balance: string | null
    error: string | null
  }> {
    return apiClient.post(
      `${PHONE_PROVIDERS_PATH}/africastalking/validate-credentials`,
      payload,
    )
  }

  async checkAfricasTalkingCallbackReachability(url: string): Promise<{
    url: string
    status: string
    reachable: boolean
    http_status_code: number | null
    error: string | null
  }> {
    return apiClient.post(
      `${PHONE_PROVIDERS_PATH}/africastalking/check-callback-reachability`,
      { url },
    )
  }
}

export const phoneService = new PhoneRegistryService()
