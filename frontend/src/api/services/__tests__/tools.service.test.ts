jest.mock('@/api/client', () => ({
  apiClient: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}))

import {
  buildCustomToolMetadata,
  getCustomToolAciDraftWarnings,
  getCustomToolAciStatus,
} from '@/api/services/tools.service'

describe('buildCustomToolMetadata', () => {
  it('scaffolds lightweight ACI metadata from the minimal authoring fields', () => {
    const metadata = buildCustomToolMetadata({
      display_name: 'Billing API',
      description: 'Looks up invoice balances from the billing backend.',
      http_method: 'GET',
      endpoint_path: '/invoices/{invoice_id}',
      read_only: true,
    })

    expect(metadata).toMatchObject({
      purpose: 'Looks up invoice balances from the billing backend.',
      when_to_use: [
        'Use when the agent needs live Billing API data from GET /invoices/{invoice_id} before answering.',
      ],
      when_not_to_use: [
        'Do not use when internal knowledge, cached state, or an already-known answer is sufficient.',
      ],
      output_validation_mode: 'warn',
    })
    expect(metadata.failure_modes).toEqual([
      expect.objectContaining({
        kind: 'transient_upstream_error',
        retryable: true,
      }),
      expect.objectContaining({
        kind: 'permanent_upstream_error',
        retryable: false,
      }),
    ])
    expect(metadata._aci).toMatchObject({
      purpose_source: 'scaffold',
      when_to_use_source: 'scaffold',
      when_not_to_use_source: 'scaffold',
      scaffolding_used: true,
    })
  })

  it('respects explicit author guidance when provided', () => {
    const metadata = buildCustomToolMetadata({
      display_name: 'CRM Writeback',
      http_method: 'POST',
      endpoint_path: '/contacts',
      read_only: false,
      purpose: 'Create or update CRM contacts after the runtime authorizes the action.',
      use_when: 'Use when the user explicitly asked to create or update a CRM contact.',
      avoid_when: 'Do not use for speculative lookups or when the user has not confirmed the write.',
    })

    expect(metadata).toMatchObject({
      purpose: 'Create or update CRM contacts after the runtime authorizes the action.',
      when_to_use: ['Use when the user explicitly asked to create or update a CRM contact.'],
      when_not_to_use: ['Do not use for speculative lookups or when the user has not confirmed the write.'],
    })
    expect(metadata._aci).toMatchObject({
      purpose_source: 'author',
      when_to_use_source: 'author',
      when_not_to_use_source: 'author',
      scaffolding_used: false,
    })
  })
})

describe('getCustomToolAciDraftWarnings', () => {
  it('returns lightweight warnings for scaffolded guidance', () => {
    const warnings = getCustomToolAciDraftWarnings({
      display_name: 'CRM Writeback',
      http_method: 'POST',
      endpoint_path: '/contacts',
      read_only: false,
    })

    expect(warnings.map((warning) => warning.code)).toEqual([
      'purpose_scaffolded',
      'use_when_scaffolded',
      'avoid_when_recommended',
    ])
  })
})

describe('getCustomToolAciStatus', () => {
  it('returns scaffolded status when metadata indicates defaults were used', () => {
    expect(getCustomToolAciStatus({ _aci: { scaffolding_used: true } })).toEqual({
      label: 'guided defaults',
      variant: 'scaffolded',
    })
  })

  it('returns authored status when the guidance came from the author', () => {
    expect(getCustomToolAciStatus({ _aci: { scaffolding_used: false } })).toEqual({
      label: 'author guided',
      variant: 'authored',
    })
  })
})
