import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

jest.mock('@/api/services/phone.service', () => ({
  phoneService: {
    listPhoneNumbers: jest.fn(),
    getPhoneNumberDetail: jest.fn(),
    createPhoneNumber: jest.fn(),
    updatePhoneNumber: jest.fn(),
    createPhoneRoute: jest.fn(),
    updatePhoneRoute: jest.fn(),
    listPhoneAuditEvents: jest.fn(),
    reconcilePhoneBindings: jest.fn(),
    importTelnyxNumber: jest.fn(),
    lookupTelnyxAvailableNumbers: jest.fn(),
    syncTelnyxBinding: jest.fn(),
    importAfricasTalkingNumber: jest.fn(),
    syncAfricasTalkingBinding: jest.fn(),
  },
}))

jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    listAgents: jest.fn(),
  },
}))

jest.mock('@/layouts/dashboard-layout', () => ({
  DashboardLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

jest.mock('sonner', () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
  },
}))

import PhoneNumbersPage from '@/pages/phone-numbers'
import { phoneService } from '@/api/services/phone.service'
import { agentDefinitionService } from '@/api/services/agent-definition.service'

const mockedPhoneService = jest.mocked(phoneService)
const mockedAgentDefinitionService = jest.mocked(agentDefinitionService)

function renderPhoneNumbersPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PhoneNumbersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('PhoneNumbersPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedAgentDefinitionService.listAgents.mockResolvedValue([
      {
        id: 'sales_agent',
        name: 'Demo Sales',
        version: '1',
        step_count: 4,
        description: 'Demo sales qualification flow',
        agent_type: 'voice',
        llm_provider: 'vertex',
        llm_model: 'gemini-3-flash-preview',
        knowledge_base_count: 0,
        has_draft_version: true,
        has_published_version: true,
        has_unpublished_changes: false,
        updated_at: '2026-04-11T10:00:00Z',
      },
    ])
    mockedPhoneService.listPhoneAuditEvents.mockResolvedValue([])
  })

  it('renders registry detail with route and provider state', async () => {
    mockedPhoneService.listPhoneNumbers.mockResolvedValue([
      {
        phone_number_id: 'pn_1',
        organization_id: 'org-1',
        e164_number: '+2348012345678',
        display_name: 'Nigeria support line',
        country_code: 'NG',
        status: 'active',
        ownership_mode: 'provider_managed',
        metadata: {},
        created_at: '2026-04-11T10:00:00Z',
        updated_at: '2026-04-11T10:00:00Z',
      },
    ])
    mockedPhoneService.getPhoneNumberDetail.mockResolvedValue({
      number: {
        phone_number_id: 'pn_1',
        organization_id: 'org-1',
        e164_number: '+2348012345678',
        display_name: 'Nigeria support line',
        country_code: 'NG',
        status: 'active',
        ownership_mode: 'provider_managed',
        metadata: {},
        created_at: '2026-04-11T10:00:00Z',
        updated_at: '2026-04-11T10:00:00Z',
      },
      bindings: [
        {
          binding_id: 'pnb_1',
          phone_number_id: 'pn_1',
          organization_id: 'org-1',
          channel: 'phone',
          provider: 'africastalking',
          provider_resource_id: '+2348012345678',
          capabilities: ['voice_inbound'],
          verification_status: 'manual_required',
          health_status: 'degraded',
          is_active: true,
          transport_metadata: {
            africastalking: {
              provider_resource_id: '+2348012345678',
              phone_number: '+2348012345678',
              account_username: 'sandbox',
              voice_callback_url: 'trunk:livekit.example.test',
              credentials_reference: 'ops/africastalking/main',
              sip_auth_required: true,
              ip_whitelist_confirmed: false,
              sip_forwarding_confirmed: false,
              configuration_confirmed: false,
              manual_requirements: [
                'confirm_sip_forwarding',
                'confirm_ip_whitelist',
                'confirm_provider_configuration',
              ],
              recommended_actions: [
                'configure_events_callback_url',
                'record_sip_trunk_target',
              ],
            },
          },
          created_at: '2026-04-11T10:00:00Z',
          updated_at: '2026-04-11T10:00:00Z',
        },
      ],
      routes: [
        {
          route_id: 'pnr_1',
          phone_number_id: 'pn_1',
          organization_id: 'org-1',
          channel: 'phone',
          agent_id: 'sales_agent',
          priority: 100,
          enabled: true,
          metadata: { purpose: 'sales_primary' },
          created_at: '2026-04-11T10:00:00Z',
          updated_at: '2026-04-11T10:00:00Z',
        },
      ],
    })
    mockedPhoneService.listPhoneAuditEvents.mockResolvedValue([
      {
        audit_event_id: 'pna_1',
        organization_id: 'org-1',
        phone_number_id: 'pn_1',
        actor_type: 'user',
        actor_user_id: 'user-admin',
        action: 'phone.binding.reconciled',
        resource_type: 'phone_number_binding',
        resource_id: 'pnb_1',
        summary: 'Phone binding reconciliation updated africastalking state',
        payload: {},
        ip_address: null,
        user_agent: null,
        created_at: '2026-04-11T11:00:00Z',
      },
    ])

    renderPhoneNumbersPage()

    expect(await screen.findByText('Nigeria support line')).toBeInTheDocument()
    expect((await screen.findAllByText('Demo Sales')).length).toBeGreaterThan(0)
    expect(await screen.findByText('Confirm Sip Forwarding')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save AT State' })).toBeInTheDocument()
    expect(screen.getByText('Audit Trail')).toBeInTheDocument()
    expect(screen.getByText('Phone binding reconciliation updated africastalking state')).toBeInTheDocument()
  })

  it('submits a new manual registry record', async () => {
    mockedPhoneService.listPhoneNumbers.mockResolvedValue([])
    mockedPhoneService.getPhoneNumberDetail.mockResolvedValue({
      number: {
        phone_number_id: 'pn_new',
        organization_id: 'org-1',
        e164_number: '+14155550123',
        display_name: 'US backup line',
        country_code: 'US',
        status: 'active',
        ownership_mode: 'imported',
        metadata: {},
        created_at: '2026-04-11T10:00:00Z',
        updated_at: '2026-04-11T10:00:00Z',
      },
      bindings: [],
      routes: [],
    })
    mockedPhoneService.createPhoneNumber.mockResolvedValue({
      phone_number_id: 'pn_new',
      organization_id: 'org-1',
      e164_number: '+14155550123',
      display_name: 'US backup line',
      country_code: 'US',
      status: 'active',
      ownership_mode: 'imported',
      metadata: { note: 'Pilot line' },
      created_at: '2026-04-11T10:00:00Z',
      updated_at: '2026-04-11T10:00:00Z',
    })

    renderPhoneNumbersPage()

    const addButtons = await screen.findAllByRole('button', { name: 'Add Number' })
    fireEvent.click(addButtons[0])

    fireEvent.change(screen.getByLabelText('E.164 Number'), {
      target: { value: '+14155550123' },
    })
    fireEvent.change(screen.getByLabelText('Display Name'), {
      target: { value: 'US backup line' },
    })
    fireEvent.change(screen.getByLabelText('Registry Note'), {
      target: { value: 'Pilot line' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create Registry Record' }))

    await waitFor(() => {
      expect(mockedPhoneService.createPhoneNumber).toHaveBeenCalledWith({
        e164_number: '+14155550123',
        display_name: 'US backup line',
        ownership_mode: 'imported',
        status: 'active',
        metadata: { note: 'Pilot line' },
      })
    })
  })
})
