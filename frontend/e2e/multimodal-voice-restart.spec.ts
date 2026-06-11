import { test, expect, type Page } from '@playwright/test'

const agentId = '44444444-4444-4444-8444-444444444444'
const canvasVersionId = '55555555-5555-4555-8555-555555555555'
const sharedConversationId = 'conv-shared-e2e-1'

async function installBrowserMocks(page: Page) {
  await page.addInitScript(({ conversationId }) => {
    (window as typeof window & { __RUHU_E2E_SENT_MESSAGES__?: unknown[] }).__RUHU_E2E_SENT_MESSAGES__ = []

    class FakeWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      url: string
      readyState = FakeWebSocket.CONNECTING
      onopen: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onclose: ((event: CloseEvent) => void) | null = null
      onerror: ((event: Event) => void) | null = null

      constructor(url: string) {
        this.url = url
        setTimeout(() => {
          this.readyState = FakeWebSocket.OPEN
          this.onopen?.(new Event('open'))
        }, 0)
      }

      send(payload: string) {
        const message = JSON.parse(payload)
        if (message.type === 'auth') {
          setTimeout(() => {
            this.onmessage?.(new MessageEvent('message', {
              data: JSON.stringify({
                type: 'connected',
                data: {
                  socket_id: 'e2e-socket',
                  conversation_id: conversationId,
                  user_id: 'e2e-user',
                },
              }),
            }))
          }, 0)
          return
        }

        if (message.type === 'message') {
          (window as typeof window & { __RUHU_E2E_SENT_MESSAGES__?: unknown[] }).__RUHU_E2E_SENT_MESSAGES__?.push(message.data)
          setTimeout(() => {
            this.onmessage?.(new MessageEvent('message', {
              data: JSON.stringify({
                type: 'message_sent',
                data: {
                  message_id: `msg-${Date.now()}`,
                  status: 'sent',
                  conversation_id: conversationId,
                  timestamp: new Date().toISOString(),
                },
              }),
            }))
          }, 0)
        }
      }

      close() {
        this.readyState = FakeWebSocket.CLOSED
        this.onclose?.(new CloseEvent('close'))
      }

      addEventListener() {}
      removeEventListener() {}
    }

    Object.defineProperty(window, 'WebSocket', {
      configurable: true,
      writable: true,
      value: FakeWebSocket,
    })

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          const tracks = [{ stop() {} }]
          return {
            getTracks: () => tracks,
            getAudioTracks: () => tracks,
            getVideoTracks: () => [],
          }
        },
      },
    })

    window.__RUHU_E2E_MOCK_VOICE__ = {
      enabled: true,
      onSendText: () => {},
      onRoomMounted: () => {},
    }
  }, { conversationId: sharedConversationId })
}

async function loginViaMock(page: Page) {
  await page.goto('/login')
  await page.getByLabel('Email Address').fill('e2e@example.com')
  await page.getByLabel('Password').fill('password123')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await page.waitForURL('**/dashboard')
}

test('end call, send chat, restart call, and keep the shared conversation id', async ({ page }) => {
  await installBrowserMocks(page)

  const voiceSessionBodies: Array<Record<string, unknown>> = []
  let voiceSessionCount = 0

  await page.route('**/api/v1/auth/login', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'e2e-token',
        token_type: 'bearer',
        user: {
          id: 'e2e-user',
          email: 'e2e@example.com',
          full_name: 'E2E User',
          organization_id: 'e2e-org',
          organization: {
            id: 'e2e-org',
            name: 'E2E Org',
          },
          role: 'admin',
        },
        organization: {
          id: 'e2e-org',
          name: 'E2E Org',
        },
      }),
    })
  })

  await page.route('**/api/v1/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'e2e-user',
        email: 'e2e@example.com',
        full_name: 'E2E User',
        organization_id: 'e2e-org',
        organization: {
          id: 'e2e-org',
          name: 'E2E Org',
        },
        role: 'admin',
      }),
    })
  })

  await page.route('**/api/v1/auth/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'e2e-token-refreshed',
        token_type: 'bearer',
      }),
    })
  })

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()

    if (path === '/api/v1/auth/login' || path === '/api/v1/auth/me' || path === '/api/v1/auth/refresh') {
      await route.fallback()
      return
    }

    if (method === 'GET' && path === `/api/v1/agents/${agentId}`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: agentId,
          organization_id: 'e2e-org',
          name: 'E2E Multimodal Agent',
          description: 'Voice restart continuity',
          agent_type: 'multimodal',
          status: 'active',
          system_prompt: 'You are a helpful assistant.',
          llm_config: { provider: 'openai', model: 'gpt-4o-mini', temperature: 0.7 },
          voice_config: { voice_id: 'alloy' },
          active_canvas_version_id: canvasVersionId,
          knowledge_base_ids: [],
          deployment_gate_enabled: false,
        }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/agents') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === `/api/v1/agents/${agentId}/deploy-readiness`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          agent_id: agentId,
          canvas_version_id: canvasVersionId,
          gate_enabled: false,
          passed: true,
          blocking_reasons: [],
          checks: [],
          runtime_telemetry: {
            has_recent_activity: false,
            lookback_days: 7,
            step_transition_count: 0,
            conversation_summary_count: 0,
            avg_step_latency_ms: null,
            tool_error_count: 0,
            channels: [],
            last_event_at: null,
          },
          evaluated_at: '2026-03-30T00:00:00Z',
          gate_config: {
            deployment_gate_enabled: false,
            min_pass_rate: 0.8,
            min_simulation_runs: 20,
            max_test_staleness_hours: 72,
          },
        }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/notifications') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === '/api/v1/dashboard/stats') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      return
    }

    if (method === 'GET' && path === '/api/v1/voice-sessions') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === '/api/v1/voice-sessions/active/count') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ active_sessions: 0 }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/insights') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      return
    }

    if (method === 'GET' && path === '/api/v1/insights/recommendations') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === '/api/v1/conversations') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === '/api/v1/journeys') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ journeys: [], total_count: 0, page: 1, page_size: 20 }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/journey-definitions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ definitions: [] }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/journey-runtime/status') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          queued_jobs: 0,
          running_jobs: 0,
          completed_jobs: 0,
          failed_jobs: 0,
          embedded_worker_enabled: false,
          last_error: null,
          job_metrics: [],
          alerts: [],
          recent_jobs: [],
        }),
      })
      return
    }

    if (method === 'GET' && path.startsWith('/api/v1/journey-analytics/')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      return
    }

    if (method === 'GET' && path === '/api/v1/canvas/versions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: canvasVersionId,
            agent_id: agentId,
            organization_id: 'e2e-org',
            name: 'Version 1',
            description: 'seed',
            version_number: 1,
            status: 'published',
            canvas_data: {},
            viewport: { x: 0, y: 0, zoom: 1 },
            created_at: '2026-03-30T00:00:00Z',
            updated_at: '2026-03-30T00:00:00Z',
          },
        ]),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/canvas/versions/${canvasVersionId}`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: canvasVersionId,
          agent_id: agentId,
          organization_id: 'e2e-org',
          name: 'Version 1',
          description: 'seed',
          version_number: 1,
          status: 'published',
          canvas_data: {},
          viewport: { x: 0, y: 0, zoom: 1 },
          created_at: '2026-03-30T00:00:00Z',
          updated_at: '2026-03-30T00:00:00Z',
        }),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/canvas/versions/${canvasVersionId}/scenario-document`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          canvas_version_id: canvasVersionId,
          authoring_mode: 'scenario',
          document: {
            entry_scenario_id: '',
            scenarios: [],
          },
        }),
      })
      return
    }

    if (method === 'GET' && (path === '/api/v1/canvas/nodes' || path === '/api/v1/canvas/edges')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === '/api/v1/voice-sessions/health') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          voice_available: true,
          livekit_reachable: true,
          mock: true,
        }),
      })
      return
    }

    if (method === 'POST' && path === '/api/v1/voice-sessions') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      voiceSessionBodies.push(body)
      voiceSessionCount += 1
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: `voice-session-${voiceSessionCount}`,
          organization_id: 'e2e-org',
          agent_id: agentId,
          conversation_id: String(body.conversation_id || sharedConversationId),
          canvas_version_id: canvasVersionId,
          room_name: `voice-room-${voiceSessionCount}`,
          livekit_room_sid: null,
          status: 'active',
          started_at: '2026-03-30T00:00:00Z',
          ended_at: null,
          duration_seconds: null,
          access_token: 'mock-token',
          connection_url: 'ws://mock-livekit.invalid',
          metadata: {},
        }),
      })
      return
    }

    if (method === 'DELETE' && path.startsWith('/api/v1/voice-sessions/')) {
      await route.fulfill({ status: 204, body: '' })
      return
    }

    if (method === 'POST' && path === '/api/v1/chat/attachments/upload') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          attachment_id: 'attachment-1',
          filename: 'Ijidai_CV_Mar-2026.pdf',
          mime_type: 'application/pdf',
          size_bytes: 64,
        }),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/atlas/agents/${agentId}/enabled`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: false }),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/atlas/agents/${agentId}/history`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ conversations: [], total: 0, agent_id: agentId }),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await loginViaMock(page)
  await page.goto(`/agents/${agentId}/canvas`)

  await page.getByRole('button', { name: 'Test', exact: true }).click()
  await expect(page.getByTitle('Start voice call')).toBeEnabled()
  await page.getByTitle('Start voice call').click()
  await expect.poll(() => voiceSessionBodies.length).toBe(1)
  expect(voiceSessionBodies[0]?.conversation_id).toBe(sharedConversationId)
  await expect(page.getByTitle('End call')).toBeVisible()
  await expect(page.getByTitle('Attach file')).toBeDisabled()
  await page.waitForTimeout(500)

  const input = page.getByPlaceholder('Type a message...')
  await input.fill('nwatam@gmail.com')
  await input.press('Enter')
  await expect(page.getByText('nwatam@gmail.com')).toBeVisible()

  await page.getByTitle('End call').click()
  await expect(page.getByTitle('Start voice call')).toBeVisible()
  await expect(page.getByTitle('Attach file')).toBeEnabled()

  await page.locator('input[type="file"]').setInputFiles({
    name: 'Ijidai_CV_Mar-2026.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4\n% E2E attachment\n'),
  })
  await expect(page.getByText('Ijidai_CV_Mar-2026.pdf')).toBeVisible()

  await input.fill('here is my CV attached')
  await input.press('Enter')
  await expect(page.getByText('here is my CV attached')).toBeVisible()
  await expect(page.getByText('Attachments: Ijidai_CV_Mar-2026.pdf')).toBeVisible()

  await expect.poll(async () => {
    const sentMessages = await page.evaluate(() => (
      (window as typeof window & { __RUHU_E2E_SENT_MESSAGES__?: Array<{ attachments?: string[] }> }).__RUHU_E2E_SENT_MESSAGES__ || []
    ))
    return sentMessages.length
  }).toBe(1)
  await expect.poll(async () => {
    const sentMessages = await page.evaluate(() => (
      (window as typeof window & { __RUHU_E2E_SENT_MESSAGES__?: Array<{ attachments?: string[] }> }).__RUHU_E2E_SENT_MESSAGES__ || []
    ))
    return sentMessages[0]?.attachments?.[0] || null
  }).toBe('attachment-1')

  await page.getByTitle('Start voice call').click()
  await expect.poll(() => voiceSessionBodies.length).toBe(2)
  expect(voiceSessionBodies[1]?.conversation_id).toBe(sharedConversationId)
  expect(voiceSessionBodies[1]?.conversation_id).toBe(voiceSessionBodies[0]?.conversation_id)
  await expect(page.getByTitle('End call')).toBeVisible()
  await page.waitForTimeout(500)

  await input.fill('can you still see the same conversation?')
  await input.press('Enter')
  await expect(page.getByText('can you still see the same conversation?')).toBeVisible()
  await expect(page.getByText('nwatam@gmail.com')).toBeVisible()
  await expect(page.getByText('here is my CV attached')).toBeVisible()
  await expect(page.getByText('Attachments: Ijidai_CV_Mar-2026.pdf')).toBeVisible()
})
