import { test, expect, type Page } from '@playwright/test'

async function loginViaMock(page: Page) {
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
          role: 'admin',
        },
      }),
    })
  })

  await page.goto('/login')
  await page.getByLabel('Email Address').fill('e2e@example.com')
  await page.getByLabel('Password').fill('password123')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await page.waitForURL('**/dashboard')
}

test('atlas apply is blocked when backend contract flags are invalid', async ({ page }) => {
  const agentId = '11111111-1111-4111-8111-111111111111'
  await loginViaMock(page)

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()

    if (method === 'GET' && path === `/api/v1/agents/${agentId}`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: agentId,
          organization_id: 'e2e-org',
          name: 'E2E Agent',
          description: 'E2E atlas gating validation',
          agent_type: 'voice',
          status: 'draft',
          system_prompt: 'You are a helpful assistant.',
          llm_config: { provider: 'openai', model: 'gpt-4o-mini', temperature: 0.7 },
          voice_config: { voice_id: 'alloy' },
          knowledge_base_ids: [],
          deployment_gate_enabled: false,
        }),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/agents/${agentId}/deploy-readiness`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          agent_id: agentId,
          gate_enabled: false,
          passed: true,
          blocking_reasons: [],
          checks: [],
          evaluated_at: '2026-02-17T00:00:00Z',
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

    if (method === 'GET' && path === '/api/v1/canvas/versions') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === `/api/v1/atlas/agents/${agentId}/enabled`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
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

    if (method === 'POST' && path === '/api/v1/atlas/workflow-chat') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: 'Generated malformed workflow',
          conversation_id: 'conv-e2e-1',
          canvas_version_id: null,
          workflow_actions: [
            {
              action: 'node_created',
              entity_type: 'node',
              entity_id: 'node_bad',
              details: {
                node_type: 'start',
                label: 'Missing temp_id',
              },
            },
          ],
          workflow_actions_contract_version: 'atlas.workflow_actions.v1',
          workflow_actions_contract_valid: false,
          workflow_actions_contract_errors: [
            "workflow_actions[0]: node_created missing required details fields ['temp_id']",
          ],
          workflow_actions_apply_ready: false,
          workflow_definition: {
            workflow_name: 'Malformed Workflow',
            workflow_description: 'E2E contract validation',
            nodes: [
              {
                temp_id: 'node_1',
                node_type: 'start',
                label: 'Start',
                description: '',
                config: {},
              },
            ],
            edges: [],
          },
          workflow_summary: {
            node_count: 1,
            edge_count: 0,
            node_types: { start: 1 },
          },
          validation_warnings: [],
          workflow_plan: null,
          phase: 'building',
          timestamp: '2026-02-17T00:00:00Z',
        }),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto(`/agents/${agentId}/canvas`)
  await page.getByRole('button', { name: 'Atlas AI', exact: true }).click()

  const atlasInput = page.getByPlaceholder('Build a workflow, paste an API URL, or ask anything...')
  await atlasInput.fill('Build a customer support workflow')
  await atlasInput.press('Enter')

  await expect(page.getByRole('button', { name: 'Apply to Canvas' })).toBeDisabled()
  await expect(
    page.getByText("workflow_actions[0]: node_created missing required details fields ['temp_id']")
  ).toBeVisible()
})

test('unsaved changes guard blocks route leave when user cancels', async ({ page }) => {
  await loginViaMock(page)

  await page.goto('/agents/new/canvas?type=voice')

  await page.getByLabel('Agent Name').fill('Unsaved E2E Change')

  page.once('dialog', async (dialog) => {
    expect(dialog.type()).toBe('confirm')
    await dialog.dismiss()
  })

  await page.getByRole('button', { name: 'Back to Agents' }).click()
  await expect(page).toHaveURL(/\/agents\/new\/canvas\?type=voice/)
})

test('build -> test -> deploy -> optimize happy path', async ({ page }) => {
  const agentId = '22222222-2222-4222-8222-222222222222'
  const canvasVersionId = '33333333-3333-4333-8333-333333333333'
  await loginViaMock(page)

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()

    if (method === 'GET' && path === `/api/v1/agents/${agentId}`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: agentId,
          organization_id: 'e2e-org',
          name: 'E2E Happy Agent',
          description: 'End-to-end happy path',
          agent_type: 'voice',
          status: 'draft',
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

    if (method === 'PATCH' && path === `/api/v1/agents/${agentId}`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: agentId,
          organization_id: 'e2e-org',
          name: 'E2E Happy Agent',
          description: 'End-to-end happy path',
          agent_type: 'voice',
          status: 'published',
          system_prompt: 'You are a helpful assistant.',
          llm_config: { provider: 'openai', model: 'gpt-4o-mini', temperature: 0.7 },
          voice_config: { voice_id: 'alloy' },
          active_canvas_version_id: canvasVersionId,
        }),
      })
      return
    }

    if (method === 'POST' && path === `/api/v1/agents/${agentId}/deploy`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      })
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
          evaluated_at: '2026-02-17T00:00:00Z',
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
            created_at: '2026-02-17T00:00:00Z',
            updated_at: '2026-02-17T00:00:00Z',
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
          created_at: '2026-02-17T00:00:00Z',
          updated_at: '2026-02-17T00:00:00Z',
        }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/canvas/nodes') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === '/api/v1/canvas/edges') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (method === 'GET' && path === `/api/v1/atlas/agents/${agentId}/enabled`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agent_id: agentId, atlas_enabled: true }),
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

    if (method === 'POST' && path === '/api/v1/atlas/workflow-chat') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: 'Generated valid workflow',
          conversation_id: 'conv-e2e-happy',
          canvas_version_id: canvasVersionId,
          workflow_actions: [
            {
              action: 'node_created',
              entity_type: 'node',
              entity_id: 'node_1',
              details: { temp_id: 'node_1', node_type: 'message', label: 'Greeting', config: {} },
            },
          ],
          workflow_actions_contract_version: 'atlas.workflow_actions.v1',
          workflow_actions_contract_valid: true,
          workflow_actions_contract_errors: [],
          workflow_actions_apply_ready: true,
          workflow_definition: {
            workflow_name: 'Happy Flow',
            workflow_description: 'E2E happy path flow',
            nodes: [
              { temp_id: 'node_1', node_type: 'message', label: 'Greeting', description: '', config: {} },
            ],
            edges: [],
          },
          workflow_summary: { node_count: 1, edge_count: 0, node_types: { message: 1 } },
          validation_warnings: [],
          workflow_plan: null,
          phase: 'building',
          timestamp: '2026-02-17T00:00:00Z',
        }),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/agents/${agentId}/insights/recommendations`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          agent_id: agentId,
          recommendations: [
            {
              recommendation_id: 'rec-1',
              agent_id: agentId,
              title: 'Reduce escalation on billing intent',
              description: 'Add a billing clarification step before transfer.',
              action_type: 'workflow_patch',
              priority: 1,
              status: 'pending',
              implementation_steps: [],
              expected_impact: {},
              suggested_config_changes: { node_type: 'condition' },
              source: 'insight_recommendation',
            },
          ],
        }),
      })
      return
    }

    if (method === 'GET' && path === `/api/v1/testing/agents/${agentId}/simulation-dashboard`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          agent_stats: {
            agent_id: agentId,
            agent_name: 'E2E Happy Agent',
            total_simulations: 12,
            simulations_passed: 10,
            simulations_failed: 2,
            pass_rate: 83.3,
            last_run_at: '2026-02-17T00:00:00Z',
            avg_response_time_ms: 950,
            total_test_cases: 4,
            active_test_cases: 4,
          },
          test_cases: [],
          recent_runs: [],
        }),
      })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto(`/agents/${agentId}/canvas`)

  // Build
  await page.getByRole('button', { name: 'Atlas AI', exact: true }).click()
  await page.getByPlaceholder('Build a workflow, paste an API URL, or ask anything...').fill('Build a greeting workflow')
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Apply to Canvas' }).click()
  await expect(page.getByText('Applied to canvas')).toBeVisible()
  await page.locator('button:has(svg.lucide-x)').first().click()
  await expect(page.getByPlaceholder('Build a workflow, paste an API URL, or ask anything...')).not.toBeVisible()

  // Test
  await page.getByRole('button', { name: 'Testing' }).click()
  await expect(page.getByText('Deployment Quality Gates')).toBeVisible()

  // Deploy
  await page.getByRole('button', { name: 'Deploy' }).click()
  await expect(page.getByText('Deploy Preflight Checks')).toBeVisible()
  await page.getByRole('button', { name: 'Deploy Now' }).click()

  // Optimize (Insights view)
  await page.getByRole('button', { name: 'Insights' }).click()
  await expect(page.getByText('Insights to changes')).toBeVisible()
  await expect(page.getByText('Reduce escalation on billing intent')).toBeVisible()
})
