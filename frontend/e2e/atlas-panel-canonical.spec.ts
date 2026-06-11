import { test, expect, type Page } from '@playwright/test'

function normalizeApiPath(path: string): string {
  return path.startsWith('/api/v1') ? (path.slice('/api/v1'.length) || '/') : path
}

function isMockableApiPath(path: string): boolean {
  if (path.startsWith('/auth/')) return true
  if (path.startsWith('/notifications')) return true
  if (path.startsWith('/canvas/')) return true
  if (path.startsWith('/atlas/')) return true
  if (path === '/agents') return true
  if (path.startsWith('/agents/') && !path.endsWith('/canvas')) return true
  return false
}

function baseAgentDocument() {
  return {
    version: '1.0',
    start_scenario_id: 'sales',
    scenarios: [
      {
        id: 'sales',
        name: 'Sales',
        start_step_id: 'discover',
        steps: [
          {
            id: 'discover',
            name: 'Discover',
            transitions: [],
            say: 'How can I help?',
          },
        ],
      },
    ],
    scenario_routes: [],
    fact_schema: [],
    agent_capability_manifest: null,
    metadata: {},
  }
}

async function fulfillCommonCanvasRoute(
  route: Parameters<Page['route']>[1] extends (route: infer T, ...args: never[]) => unknown ? T : never,
  {
    method,
    path,
    agentId,
    agentName,
    description,
  }: {
    method: string
    path: string
    agentId: string
    agentName: string
    description: string
  },
): Promise<boolean> {
  if (method === 'GET' && path === '/agents') {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: agentId,
          name: agentName,
          version: 'draft',
          step_count: 1,
          description,
          agent_type: 'voice',
          llm_provider: 'openai',
          llm_model: 'gpt-4o-mini',
          knowledge_base_count: 0,
          has_draft_version: true,
          has_published_version: false,
          has_unpublished_changes: true,
          updated_at: '2026-04-24T00:00:00Z',
          current_draft_version_id: 'draft-1',
          current_published_version_id: null,
        },
      ]),
    })
    return true
  }

  if (method === 'GET' && path === '/notifications') {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
    return true
  }

  if (method === 'POST' && path === '/notifications/mark-read-all') {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true }),
    })
    return true
  }

  if (method === 'GET' && path === `/agents/${agentId}`) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: agentId,
        organization_id: 'e2e-org',
        name: agentName,
        description,
        agent_type: 'voice',
        status: 'draft',
        system_prompt: 'You are helpful.',
        llm_config: { provider: 'openai', model: 'gpt-4o-mini', temperature: 0.7 },
        voice_config: { voice_id: 'alloy' },
        knowledge_base_ids: [],
        deployment_gate_enabled: false,
      }),
    })
    return true
  }

  if (method === 'GET' && path === `/agents/${agentId}/settings`) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agent_id: agentId,
        settings: {
          description,
          agent_type: 'voice',
          system_prompt: 'You are helpful.',
          llm_config: {
            provider: 'openai',
            model: 'gpt-4o-mini',
            temperature: 0.7,
            classifier: { mode: 'off', fallback_policy: 'bounded' },
          },
          voice_config: { voice_id: 'alloy' },
          knowledge_base_ids: [],
        },
      }),
    })
    return true
  }

  if (method === 'GET' && path === `/agents/${agentId}/agent-document`) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agent_id: agentId,
        target: 'draft',
        document: baseAgentDocument(),
      }),
    })
    return true
  }

  if (method === 'GET' && path === `/agents/${agentId}/deploy-readiness`) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agent_id: agentId,
        gate_enabled: false,
        passed: true,
        blocking_reasons: [],
        checks: [],
        evaluated_at: '2026-04-24T00:00:00Z',
        gate_config: {
          deployment_gate_enabled: false,
          min_pass_rate: 0.8,
          min_simulation_runs: 20,
          max_test_staleness_hours: 72,
        },
      }),
    })
    return true
  }

  if (method === 'GET' && path === `/agents/${agentId}/versions`) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
    return true
  }

  if (method === 'GET' && path === `/agents/${agentId}/publish-review`) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agent_id: agentId,
        draft_version_id: 'draft-1',
        published_version_id: null,
        can_publish: true,
        blockers: [],
        warnings: [],
        validation: {
          valid: true,
          error_count: 0,
          warning_count: 0,
          issues: [],
        },
        diff: null,
        available_tools: [],
        missing_tools: [],
      }),
    })
    return true
  }

  if (method === 'GET' && path === '/canvas/versions') {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    return true
  }

  if (method === 'GET' && path === '/canvas/nodes') {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    return true
  }

  if (method === 'GET' && path === '/canvas/edges') {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    return true
  }

  return false
}

async function loginViaMock(page: Page) {
  const user = {
    id: 'e2e-user',
    email: 'e2e@example.com',
    full_name: 'E2E User',
    organization_id: 'e2e-org',
    role: 'admin',
  }

  await page.addInitScript((storedUser) => {
    window.localStorage.setItem('auth-storage', JSON.stringify({
      state: { user: storedUser },
      version: 0,
    }))
  }, user)

  await page.addInitScript(() => {
    class MockEventSource {
      url: string
      withCredentials: boolean
      readyState = 0
      onopen: ((event: Event) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      private listeners = new Map<string, Set<(event: Event) => void>>()

      constructor(url: string | URL, init?: EventSourceInit) {
        this.url = String(url)
        this.withCredentials = Boolean(init?.withCredentials)
        window.setTimeout(() => {
          this.readyState = 1
          this.onopen?.(new Event('open'))
        }, 0)
      }

      addEventListener(type: string, listener: (event: Event) => void) {
        const existing = this.listeners.get(type) ?? new Set<(event: Event) => void>()
        existing.add(listener)
        this.listeners.set(type, existing)
      }

      removeEventListener(type: string, listener: (event: Event) => void) {
        this.listeners.get(type)?.delete(listener)
      }

      close() {
        this.readyState = 2
      }
    }

    Object.defineProperty(window, 'EventSource', {
      configurable: true,
      writable: true,
      value: MockEventSource,
    })
  })

  await page.route('**/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(user),
    })
  })

  await page.route('**/auth/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(user),
    })
  })
}

async function openCanonicalAtlasPanel(page: Page) {
  await page.getByRole('button', { name: 'Atlas', exact: true }).click()
  const disabledToggle = page.getByRole('button', { name: 'Disabled', exact: true })
  if (await disabledToggle.isVisible().catch(() => false)) {
    await disabledToggle.click()
  }
  await expect(page.getByText('Canonical Atlas session UI')).toBeVisible()
}

test('atlas panel resumes archived-capable session history', async ({ page }) => {
  const agentId = '55555555-5555-4555-8555-555555555555'
  const sessionId = 'atlas-session-history'
  await loginViaMock(page)

  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    const path = normalizeApiPath(url.pathname)
    const method = route.request().method()

    if (!isMockableApiPath(path)) {
      await route.continue()
      return
    }

    if (await fulfillCommonCanvasRoute(route, {
      method,
      path,
      agentId,
      agentName: 'Atlas History Agent',
      description: 'Atlas history coverage',
    })) {
      return
    }

    if (method === 'GET' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'PUT' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'GET' && path === '/atlas/sessions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          sessions: [
            {
              session_id: sessionId,
              status: 'active',
              scope: 'provisioning',
              agent_id: agentId,
              created_at: '2026-04-24T10:00:00Z',
              updated_at: '2026-04-24T10:05:00Z',
            },
          ],
          total_count: 1,
          has_more: false,
        }),
      })
      return
    }

    if (method === 'GET' && path === `/atlas/sessions/${sessionId}/messages`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          messages: [
            {
              message_id: 'msg-1',
              role: 'assistant',
              content: 'Resumed Atlas session from history.',
              sequence_number: 1,
              metadata: {},
              created_at: '2026-04-24T10:00:00Z',
            },
          ],
          has_more: false,
          total_count: 1,
        }),
      })
      return
    }

    if (method === 'GET' && path === `/atlas/sessions/${sessionId}/events`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          events: [],
          has_more: false,
          total_count: 0,
        }),
      })
      return
    }

    if (method === 'POST' && path === `/atlas/sessions/${sessionId}/archive`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          status: 'archived',
          archived_at: '2026-04-24T11:00:00Z',
        }),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto(`/agents/${agentId}/canvas`)
  await openCanonicalAtlasPanel(page)
  await expect(page.getByText('Recent Sessions')).toBeVisible()
  await page.getByRole('button', { name: 'Resume', exact: true }).click()
  await expect(page.getByText('Resumed Atlas session from history.').first()).toBeVisible()

  await page.getByRole('button', { name: 'Archive' }).evaluate((element: HTMLElement) => element.click())
  await expect(page.getByText(/archived/i).first()).toBeVisible()
})

test('atlas panel uses explicit permission and apply flow', async ({ page }) => {
  const agentId = '66666666-6666-4666-8666-666666666666'
  const sessionId = 'atlas-session-permission'
  await loginViaMock(page)

  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    const path = normalizeApiPath(url.pathname)
    const method = route.request().method()

    if (!isMockableApiPath(path)) {
      await route.continue()
      return
    }

    if (await fulfillCommonCanvasRoute(route, {
      method,
      path,
      agentId,
      agentName: 'Atlas Permission Agent',
      description: 'Atlas permission coverage',
    })) {
      return
    }

    if (method === 'GET' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'PUT' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'GET' && path === '/atlas/sessions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [], total_count: 0, has_more: false }),
      })
      return
    }

    if (method === 'POST' && path === '/atlas/sessions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          status: 'active',
          scope: 'provisioning',
          agent_id: agentId,
          created_at: '2026-04-24T10:00:00Z',
          updated_at: '2026-04-24T10:00:00Z',
        }),
      })
      return
    }

    if (method === 'GET' && path === `/atlas/sessions/${sessionId}/messages`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: sessionId, messages: [], has_more: false, total_count: 0 }),
      })
      return
    }

    if (method === 'GET' && path === `/atlas/sessions/${sessionId}/events`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: sessionId, events: [], has_more: false, total_count: 0 }),
      })
      return
    }

    if (method === 'POST' && path === '/atlas/turns') {
      const payload = route.request().postDataJSON() as
        | { review_decisions?: Array<{ delta_id: string; decision: string }> }
        | undefined
      if (payload?.review_decisions?.length) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            session_id: sessionId,
            message: 'Atlas approved the reviewed action.',
            next_action: 'blocked',
            generator: { mode: 'fallback', model: null },
            questions: [],
            dependencies: [],
            blockers: [],
            proposed_changes: {
              agent_metadata_deltas: [],
              scenario_deltas: [],
              step_deltas: [
                {
                  delta_id: 'delta-1',
                  operation: 'update',
                  status: 'approved',
                  change_type: 'rename_step',
                  depends_on_delta_ids: [],
                  payload: { target_step_id: 'discover' },
                  summary: 'Rename the discover step.',
                },
              ],
              scenario_route_deltas: [],
              channel_policy_deltas: [],
              rule_deltas: [],
              knowledge_deltas: [],
              integration_binding_deltas: [],
            },
            derived_impact: {
              compiled_runtime_preview: {},
              affected_scenarios: ['sales'],
              affected_steps: ['discover'],
              possible_entry_scenario_changes: [],
              possible_tool_execution_changes: [],
              possible_publish_readiness_changes: [],
            },
            validation: { status: 'passed', blocking: false, errors: [], warnings: [], checks: [] },
            provisioning_manifest: [],
            api_discovery_results: [],
            attachment_ingestion_results: [],
            references: {
              agent_ids: ['sales'],
              agent_version_ids: [],
              scenario_ids: ['sales'],
              step_ids: ['discover'],
              conversation_ids: [],
              trace_ids: [],
              rule_ids: [],
              tool_refs: [],
            },
            review_state: {
              approved_delta_ids: ['delta-1'],
              rejected_delta_ids: [],
              pending_delta_ids: [],
              latest_apply_request_id: null,
            },
            pending_permission_requests: [
              {
                request_id: 'perm-1',
                kind: 'apply',
                status: 'pending',
                reason: 'Applying this action changes the draft agent.',
                risk_summary: 'This updates authored behavior.',
                scope_ref: { session_id: sessionId },
                delta_ids: ['delta-1'],
                requested_actions: ['apply_delta'],
                created_at: '2026-04-24T10:00:00Z',
                expires_at: null,
              },
            ],
          }),
        })
        return
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          message: 'Atlas prepared a reviewed action.',
          next_action: 'ready_to_review_changes',
          generator: { mode: 'fallback', model: null },
          questions: [],
          dependencies: [],
          blockers: [],
          proposed_changes: {
            agent_metadata_deltas: [],
            scenario_deltas: [],
            step_deltas: [
              {
                delta_id: 'delta-1',
                operation: 'update',
                status: 'proposed',
                change_type: 'rename_step',
                depends_on_delta_ids: [],
                payload: { target_step_id: 'discover' },
                summary: 'Rename the discover step.',
              },
            ],
            scenario_route_deltas: [],
            channel_policy_deltas: [],
            rule_deltas: [],
            knowledge_deltas: [],
            integration_binding_deltas: [],
          },
          derived_impact: {
            compiled_runtime_preview: {},
            affected_scenarios: ['sales'],
            affected_steps: ['discover'],
            possible_entry_scenario_changes: [],
            possible_tool_execution_changes: [],
            possible_publish_readiness_changes: [],
          },
          validation: { status: 'passed', blocking: false, errors: [], warnings: [], checks: [] },
          provisioning_manifest: [],
          api_discovery_results: [],
          attachment_ingestion_results: [],
          references: {
            agent_ids: ['sales'],
            agent_version_ids: [],
            scenario_ids: ['sales'],
            step_ids: ['discover'],
            conversation_ids: [],
            trace_ids: [],
            rule_ids: [],
            tool_refs: [],
          },
          review_state: {
            approved_delta_ids: [],
            rejected_delta_ids: [],
            pending_delta_ids: ['delta-1'],
            latest_apply_request_id: null,
          },
          pending_permission_requests: [],
        }),
      })
      return
    }

    if (method === 'POST' && path === `/atlas/sessions/${sessionId}/permission-decisions`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          updated_requests: [
            {
              request_id: 'perm-1',
              kind: 'apply',
              status: 'approved',
              reason: 'Applying this action changes the draft agent.',
              risk_summary: 'This updates authored behavior.',
              scope_ref: { session_id: sessionId },
              delta_ids: ['delta-1'],
              requested_actions: ['apply_delta'],
              created_at: '2026-04-24T10:00:00Z',
              expires_at: null,
            },
          ],
        }),
      })
      return
    }

    if (method === 'POST' && path === `/atlas/sessions/${sessionId}/apply`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          apply_request_id: 'apply-1',
          session_id: sessionId,
          status: 'applied',
          error: null,
        }),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto(`/agents/${agentId}/canvas`)
  await openCanonicalAtlasPanel(page)

  await page.getByPlaceholder('Ask Atlas to connect, import, review, or repair integrations...').fill('Prepare a reviewed integration update')
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Approve Reviewed Actions' }).evaluate((element: HTMLElement) => element.click())
  await expect(page.getByText('Applying this action changes the draft agent.').first()).toBeVisible()
  await page.getByRole('button', { name: 'Approve Permission' }).evaluate((element: HTMLElement) => element.click())
  await page.getByRole('button', { name: 'Apply Approved Actions' }).evaluate((element: HTMLElement) => element.click())
  await expect(page.getByText('Applied 1 approved Atlas action.').first()).toBeVisible()
})

test('atlas panel shows cross-scope sessions and validation and operations focus cards', async ({ page }) => {
  const agentId = '77777777-7777-4777-8777-777777777777'
  const validationSessionId = 'atlas-validation-session'
  const operationsSessionId = 'atlas-operations-session'
  await loginViaMock(page)

  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    const path = normalizeApiPath(url.pathname)
    const method = route.request().method()

    if (!isMockableApiPath(path)) {
      await route.continue()
      return
    }

    if (await fulfillCommonCanvasRoute(route, {
      method,
      path,
      agentId,
      agentName: 'Atlas Validation Agent',
      description: 'Atlas validation and operations coverage',
    })) {
      return
    }

    if (method === 'GET' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'PUT' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'GET' && path === '/atlas/sessions') {
      const scope = url.searchParams.get('scope')
      if (scope === 'validation') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            sessions: [
              {
                session_id: validationSessionId,
                status: 'active',
                scope: 'validation',
                agent_id: agentId,
                created_at: '2026-04-24T09:00:00Z',
                updated_at: '2026-04-24T09:10:00Z',
              },
            ],
            total_count: 1,
            has_more: false,
          }),
        })
        return
      }
      if (scope === 'operations') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            sessions: [
              {
                session_id: operationsSessionId,
                status: 'active',
                scope: 'operations',
                agent_id: agentId,
                created_at: '2026-04-24T08:00:00Z',
                updated_at: '2026-04-24T08:20:00Z',
              },
            ],
            total_count: 1,
            has_more: false,
          }),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          sessions: [
            {
              session_id: validationSessionId,
              status: 'active',
              scope: 'validation',
              agent_id: agentId,
              created_at: '2026-04-24T09:00:00Z',
              updated_at: '2026-04-24T09:10:00Z',
            },
            {
              session_id: operationsSessionId,
              status: 'active',
              scope: 'operations',
              agent_id: agentId,
              created_at: '2026-04-24T08:00:00Z',
              updated_at: '2026-04-24T08:20:00Z',
            },
          ],
          total_count: 2,
          has_more: false,
        }),
      })
      return
    }

    if (method === 'POST' && path === '/atlas/sessions') {
      const payload = route.request().postDataJSON() as { scope: string }
      const sessionId = payload.scope === 'validation' ? validationSessionId : operationsSessionId
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          status: 'active',
          scope: payload.scope,
          agent_id: agentId,
          created_at: '2026-04-24T10:00:00Z',
          updated_at: '2026-04-24T10:00:00Z',
        }),
      })
      return
    }

    if (method === 'GET' && path.startsWith('/atlas/sessions/') && path.endsWith('/messages')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: path.split('/')[5], messages: [], has_more: false, total_count: 0 }),
      })
      return
    }

    if (method === 'GET' && path.startsWith('/atlas/sessions/') && path.endsWith('/events')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: path.split('/')[5], events: [], has_more: false, total_count: 0 }),
      })
      return
    }

    if (method === 'POST' && path === '/atlas/turns') {
      const payload = route.request().postDataJSON() as { session_id: string }
      if (payload.session_id === validationSessionId) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            session_id: validationSessionId,
            message: 'Atlas summarized validation blockers.',
            next_action: 'ready_to_validate',
            generator: { mode: 'fallback', model: null },
            questions: [],
            dependencies: [],
            blockers: [],
            proposed_changes: {
              agent_metadata_deltas: [],
              scenario_deltas: [],
              step_deltas: [],
              scenario_route_deltas: [],
              channel_policy_deltas: [],
              rule_deltas: [],
              knowledge_deltas: [],
              integration_binding_deltas: [],
            },
            derived_impact: {
              compiled_runtime_preview: {},
              affected_scenarios: ['sales'],
              affected_steps: ['discover'],
              possible_entry_scenario_changes: [],
              possible_tool_execution_changes: [],
              possible_publish_readiness_changes: ['publish review may improve'],
            },
            validation: {
              status: 'failed',
              blocking: true,
              errors: ['Step discover still needs review.'],
              warnings: ['Knowledge review is recommended.'],
              checks: [
                {
                  code: 'publish.readiness',
                  scope: 'publish',
                  status: 'failed',
                  message: 'Draft has unresolved issues.',
                  reference_ids: ['discover'],
                },
              ],
            },
            provisioning_manifest: [],
            api_discovery_results: [],
            attachment_ingestion_results: [],
            references: {
              agent_ids: ['sales'],
              agent_version_ids: [],
              scenario_ids: ['sales'],
              step_ids: ['discover'],
              conversation_ids: [],
              trace_ids: [],
              rule_ids: [],
              tool_refs: [],
            },
            review_state: {
              approved_delta_ids: [],
              rejected_delta_ids: [],
              pending_delta_ids: [],
              latest_apply_request_id: null,
            },
            pending_permission_requests: [],
          }),
        })
        return
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: operationsSessionId,
          message: 'Atlas reviewed operational blockers.',
          next_action: 'blocked',
          generator: { mode: 'fallback', model: null },
          questions: [],
          dependencies: [
            {
              key: 'crm_conn',
              kind: 'integration',
              display_name: 'CRM Connection',
              status: 'requires_auth',
              blocking: true,
              reason: 'Connection token expired.',
              suggested_action: 'Reconnect the CRM integration.',
              reference_ids: ['crm'],
            },
          ],
          blockers: [],
          proposed_changes: {
            agent_metadata_deltas: [],
            scenario_deltas: [],
            step_deltas: [],
            scenario_route_deltas: [],
            channel_policy_deltas: [],
            rule_deltas: [],
            knowledge_deltas: [],
            integration_binding_deltas: [],
          },
          derived_impact: {
            compiled_runtime_preview: {},
            affected_scenarios: [],
            affected_steps: [],
            possible_entry_scenario_changes: [],
            possible_tool_execution_changes: ['crm lookup may fail'],
            possible_publish_readiness_changes: [],
          },
          validation: { status: 'not_run', blocking: false, errors: [], warnings: [], checks: [] },
          provisioning_manifest: [],
          api_discovery_results: [],
          attachment_ingestion_results: [],
          references: {
            agent_ids: ['sales'],
            agent_version_ids: [],
            scenario_ids: [],
            step_ids: [],
            conversation_ids: [],
            trace_ids: ['trace-1'],
            rule_ids: [],
            tool_refs: ['crm.lookup'],
          },
          review_state: {
            approved_delta_ids: [],
            rejected_delta_ids: [],
            pending_delta_ids: [],
            latest_apply_request_id: null,
          },
          pending_permission_requests: [],
        }),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto(`/agents/${agentId}/canvas`)
  await openCanonicalAtlasPanel(page)

  await expect(page.getByText('Recent Across Scopes')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Resume Validate' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Resume Operate' })).toBeVisible()

  await page.getByRole('button', { name: 'Validate', exact: true }).click()
  await page.getByPlaceholder('Ask Atlas to review and help with this agent scope...').fill('Summarize validation blockers before publish.')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Publish Readiness Focus')).toBeVisible()

  await page.getByRole('button', { name: 'Operate', exact: true }).click()
  await page.getByPlaceholder('Ask Atlas to review and help with this agent scope...').fill('Review recent operational blockers for this agent.')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Operational Focus')).toBeVisible()
  await expect(page.getByText(/Blocking dependencies:/)).toBeVisible()
})

test('atlas panel renders low-frequency Atlas payloads cleanly', async ({ page }) => {
  const agentId = '88888888-8888-4888-8888-888888888888'
  const sessionId = 'atlas-edge-session'
  await loginViaMock(page)

  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    const path = normalizeApiPath(url.pathname)
    const method = route.request().method()

    if (!isMockableApiPath(path)) {
      await route.continue()
      return
    }

    if (await fulfillCommonCanvasRoute(route, {
      method,
      path,
      agentId,
      agentName: 'Atlas Edge Agent',
      description: 'Atlas low-frequency payload coverage',
    })) {
      return
    }

    if (method === 'GET' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'PUT' && path.startsWith(`/atlas/agents/${agentId}/enabled`)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
      })
      return
    }

    if (method === 'GET' && path === '/atlas/sessions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [], total_count: 0, has_more: false }),
      })
      return
    }

    if (method === 'POST' && path === '/atlas/sessions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          status: 'active',
          scope: 'agent_authoring',
          agent_id: agentId,
          created_at: '2026-04-24T10:00:00Z',
          updated_at: '2026-04-24T10:00:00Z',
        }),
      })
      return
    }

    if (method === 'GET' && path === `/atlas/sessions/${sessionId}/messages`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: sessionId, messages: [], has_more: false, total_count: 0 }),
      })
      return
    }

    if (method === 'GET' && path === `/atlas/sessions/${sessionId}/events`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: sessionId, events: [], has_more: false, total_count: 0 }),
      })
      return
    }

    if (method === 'POST' && path === '/atlas/turns') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          message: 'Atlas needs clarification before proceeding.',
          next_action: 'ask_questions',
          generator: { mode: 'fallback', model: null },
          questions: [
            {
              question_id: 'q-1',
              question: 'Which region should this agent support first?',
              help_text: 'Pick the first rollout region so Atlas can scope language and compliance changes.',
              options: ['Nigeria', 'Kenya', 'South Africa'],
              required: true,
              target_ref: null,
            },
          ],
          dependencies: [],
          blockers: [
            {
              code: 'atlas.missing_rollout_region',
              message: 'Atlas cannot plan rollout-specific changes until the first region is chosen.',
              blocking: true,
            },
          ],
          proposed_changes: {
            agent_metadata_deltas: [],
            scenario_deltas: [],
            step_deltas: [],
            scenario_route_deltas: [],
            channel_policy_deltas: [],
            rule_deltas: [],
            knowledge_deltas: [],
            integration_binding_deltas: [],
          },
          derived_impact: {
            compiled_runtime_preview: {},
            affected_scenarios: [],
            affected_steps: [],
            possible_entry_scenario_changes: [],
            possible_tool_execution_changes: [],
            possible_publish_readiness_changes: [],
          },
          validation: { status: 'not_run', blocking: false, errors: [], warnings: [], checks: [] },
          provisioning_manifest: [],
          api_discovery_results: [],
          attachment_ingestion_results: [
            {
              attachment_id: 'brief-1',
              mode: 'workflow_description',
              quality_flags: ['partial_parse'],
              truncated: true,
              chunk_count: 3,
              used_chunk_count: 2,
              extracted_characters: 1450,
              notes: 'Atlas used the first two chunks and ignored repeated footer content.',
            },
          ],
          references: {
            agent_ids: ['sales'],
            agent_version_ids: [],
            scenario_ids: [],
            step_ids: [],
            conversation_ids: [],
            trace_ids: [],
            rule_ids: [],
            tool_refs: [],
          },
          review_state: {
            approved_delta_ids: [],
            rejected_delta_ids: [],
            pending_delta_ids: [],
            latest_apply_request_id: null,
          },
          pending_permission_requests: [],
        }),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto(`/agents/${agentId}/canvas`)
  await openCanonicalAtlasPanel(page)
  await page.getByRole('button', { name: 'Author', exact: true }).click()
  await page.getByPlaceholder('Ask Atlas to review and help with this agent scope...').fill('Review this uploaded workflow brief')
  await page.keyboard.press('Enter')

  await expect(page.getByText('Questions', { exact: true })).toBeVisible()
  await expect(page.getByText('Which region should this agent support first?')).toBeVisible()
  await expect(page.getByText('Pick the first rollout region so Atlas can scope language and compliance changes.')).toBeVisible()
  await expect(page.getByText('Nigeria')).toBeVisible()
  await expect(page.getByText('Blockers', { exact: true })).toBeVisible()
  await expect(page.getByText('atlas.missing_rollout_region')).toBeVisible()
  await expect(page.getByText('Attachment Ingestion', { exact: true })).toBeVisible()
  await expect(page.getByText('brief-1')).toBeVisible()
  await expect(page.getByText(/Truncated:/)).toBeVisible()
})
