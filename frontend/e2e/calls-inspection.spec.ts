import { expect, test } from '@playwright/test'

const sessionId = 'session-calls-e2e-1'
const conversationId = 'conversation-calls-e2e-1'

test('opens call session detail drawer and renders conversation/event timeline', async ({ page }) => {
  await page.route('**/api/v1/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user: {
          user_id: 'calls-e2e-user',
          email: 'calls-e2e@example.com',
          display_name: 'Calls E2E',
          avatar_url: null,
          timezone: 'Africa/Lagos',
          language: 'en',
          preferences: {},
          is_superuser: false,
        },
        organization: {
          organization_id: 'calls-e2e-org',
          slug: 'calls-e2e-org',
          name: 'Calls E2E Org',
          domain: null,
          icon_url: null,
          role: 'admin',
          is_account_owner: true,
        },
        session_id: 'calls-e2e-session',
        expires_at: '2026-04-12T12:00:00Z',
      }),
    })
  })

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()

    if (path === '/api/v1/auth/me') {
      await route.fallback()
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

    if (method === 'GET' && path === '/api/v1/voice-sessions/active/count') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ active_sessions: 1 }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/voice-sessions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: sessionId,
            organization_id: 'calls-e2e-org',
            agent_id: 'calls-agent-e2e',
            agent_name: 'Calls Inspector Agent',
            conversation_id: conversationId,
            canvas_version_id: null,
            room_name: 'room-calls-e2e',
            status: 'active',
            started_at: '2026-04-11T12:00:00Z',
            ended_at: null,
            duration_seconds: null,
            metadata: {},
          },
        ]),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/voice-sessions/${sessionId}`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: sessionId,
          room_name: 'room-calls-e2e',
          status: 'active',
          num_participants: 2,
          participants: [
            {
              identity: 'user:calls-e2e',
              name: 'Calls User',
              joined_at: '2026-04-11T12:00:01Z',
            },
            {
              identity: 'agent:ruhu',
              name: 'Ruhu Agent',
              joined_at: '2026-04-11T12:00:01Z',
            },
          ],
          started_at: '2026-04-11T12:00:00Z',
          duration_seconds: 45,
        }),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/conversations/${conversationId}/traces`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            trace_id: 'trace-calls-1',
            conversation_id: conversationId,
            turn_id: 'turn-calls-1',
            agent_id: 'calls-agent-e2e',
            agent_version_id: 'calls-agent-version-e2e',
            state_before: 'discover',
            state_after: 'qualification',
            semantic_events: [],
            fact_updates: [],
            chosen_action: { type: 'reply', reason: 'mocked', payload: {} },
            emitted_messages: [{ role: 'assistant', text: 'Could you share your current team size?' }],
            tool_calls: [],
            rules: { evaluations: [] },
            latency_breakdown_ms: {},
            recorded_at: '2026-04-11T12:00:10Z',
          },
        ]),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/conversations/${conversationId}/realtime-events`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            event_id: 'evt-calls-1',
            conversation_id: conversationId,
            realtime_session_id: sessionId,
            family: 'voice',
            name: 'participant_joined',
            conversation_sequence: 1,
            actor_type: 'participant',
            actor_id: 'user:calls-e2e',
            payload: { reason: 'connected' },
            created_at: '2026-04-11T12:00:05Z',
          },
        ]),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto('/calls')

  await expect(page.getByRole('heading', { name: 'Live Calls' })).toBeVisible()
  await expect(page.getByTestId(`call-session-card-${sessionId}`)).toBeVisible()

  await page.getByTestId(`call-session-card-${sessionId}`).click()
  const drawer = page.getByTestId('calls-session-detail-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Session Details' })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Calls Inspector Agent' })).toBeVisible()
  await expect(drawer.getByText('Participants:')).toBeVisible()
  await expect(drawer.getByText('voice.participant_joined')).toBeVisible()
  await expect(drawer.getByText('Could you share your current team size?')).toBeVisible()
  await expect(drawer.getByTestId('calls-session-timeline')).toBeVisible()
  await expect(drawer.getByTestId('calls-timeline-item')).toHaveCount(2)
})
