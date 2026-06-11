import { expect, test, type Page } from '@playwright/test'

type MockDefinition = {
  definition_id: string
  organization_id: string
  slug: string
  name: string
  description: string | null
  subject_strategy: {
    kind: 'metadata_path' | 'fact_name' | 'channel_identity' | 'external_ref'
    value: string
    fallback_kind: 'metadata_path' | 'fact_name' | 'channel_identity' | 'external_ref' | null
    fallback_value: string | null
  }
  scope: {
    agent_ids: string[]
    channel_filters: string[]
    conversation_mode_filters: string[]
  }
  status: 'active' | 'archived'
  tags: string[]
  settings: Record<string, unknown>
  current_draft_version_id: string | null
  current_published_version_id: string | null
  created_by_user_id: string | null
  created_at: string
  updated_at: string
}

type MockVersion = {
  definition_version_id: string
  organization_id: string
  definition_id: string
  version_number: number
  status: 'draft' | 'published'
  based_on_version_id: string | null
  rules: Record<string, unknown>
  compiled_rules: Record<string, unknown>
  review_summary: Record<string, unknown>
  created_by_user_id: string | null
  created_at: string
  updated_at: string
  published_at: string | null
}

const organizationId = 'org-journeys-e2e'
const createdAt = '2026-04-11T12:00:00Z'

function buildDefinition(overrides: Partial<MockDefinition> = {}): MockDefinition {
  return {
    definition_id: overrides.definition_id || crypto.randomUUID(),
    organization_id: organizationId,
    slug: overrides.slug || 'existing-journey',
    name: overrides.name || 'Existing Journey',
    description: overrides.description ?? 'Existing journey description',
    subject_strategy: overrides.subject_strategy || {
      kind: 'channel_identity',
      value: 'contact',
      fallback_kind: null,
      fallback_value: null,
    },
    scope: overrides.scope || {
      agent_ids: [],
      channel_filters: [],
      conversation_mode_filters: [],
    },
    status: overrides.status || 'active',
    tags: overrides.tags || [],
    settings: overrides.settings || {},
    current_draft_version_id: overrides.current_draft_version_id ?? null,
    current_published_version_id: overrides.current_published_version_id ?? null,
    created_by_user_id: overrides.created_by_user_id ?? 'user-journeys-e2e',
    created_at: overrides.created_at || createdAt,
    updated_at: overrides.updated_at || createdAt,
  }
}

function buildVersion(overrides: Partial<MockVersion> = {}): MockVersion {
  return {
    definition_version_id: overrides.definition_version_id || crypto.randomUUID(),
    organization_id: organizationId,
    definition_id: overrides.definition_id || 'definition-missing',
    version_number: overrides.version_number || 1,
    status: overrides.status || 'draft',
    based_on_version_id: overrides.based_on_version_id ?? null,
    rules: overrides.rules || {
      entry_rules: [{ kind: 'conversation_started', metadata: {} }],
      touchpoint_rules: [],
      milestones: [],
      outcome_rules: {},
      abandonment_policy: { close_as: 'abandoned' },
      merge_policy: { reopen_statuses: [] },
    },
    compiled_rules: overrides.compiled_rules || {},
    review_summary: overrides.review_summary || {},
    created_by_user_id: overrides.created_by_user_id ?? 'user-journeys-e2e',
    created_at: overrides.created_at || createdAt,
    updated_at: overrides.updated_at || createdAt,
    published_at: overrides.published_at ?? null,
  }
}

function summarizeDefinition(definition: MockDefinition) {
  return {
    definition_id: definition.definition_id,
    organization_id: definition.organization_id,
    slug: definition.slug,
    name: definition.name,
    description: definition.description,
    status: definition.status,
    current_draft_version_id: definition.current_draft_version_id,
    current_published_version_id: definition.current_published_version_id,
    updated_at: definition.updated_at,
  }
}

function buildReadiness(definition: MockDefinition, versions: MockVersion[]) {
  const draftVersion = versions.find((version) => version.status === 'draft') || null
  const publishedVersion = versions.find((version) => version.status === 'published') || null

  return {
    definition,
    draft_version: draftVersion,
    published_version: publishedVersion,
    readiness: {
      definition_id: definition.definition_id,
      draft_version_id: draftVersion?.definition_version_id || null,
      published_version_id: publishedVersion?.definition_version_id || null,
      can_publish: !!draftVersion,
      blockers: draftVersion ? [] : [{ severity: 'error', code: 'draft_missing', message: 'Draft version required' }],
      warnings: [],
      draft_review: draftVersion
        ? {
            definition_id: definition.definition_id,
            definition_version_id: draftVersion.definition_version_id,
            can_publish: true,
            blockers: [],
            warnings: [],
            validated_at: createdAt,
          }
        : null,
      validated_at: createdAt,
    },
  }
}

async function installJourneyWorkspaceMocks(page: Page) {
  const existingDefinition = buildDefinition({
    definition_id: 'definition-existing',
    slug: 'existing-journey',
    name: 'Existing Journey',
    current_published_version_id: 'version-existing-published',
  })
  const existingPublishedVersion = buildVersion({
    definition_version_id: 'version-existing-published',
    definition_id: existingDefinition.definition_id,
    version_number: 1,
    status: 'published',
    published_at: createdAt,
  })

  const definitions: MockDefinition[] = [existingDefinition]
  const versionsByDefinition = new Map<string, MockVersion[]>([
    [existingDefinition.definition_id, [existingPublishedVersion]],
  ])

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()

    if (method === 'GET' && path === '/api/v1/auth/me') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user: {
            user_id: 'user-journeys-e2e',
            email: 'journeys@example.com',
            display_name: 'Journeys E2E',
            avatar_url: null,
            timezone: 'Africa/Lagos',
            language: 'en',
            preferences: {},
            is_superuser: false,
          },
          organization: {
            organization_id: organizationId,
            slug: 'journey-org',
            name: 'Journey Org',
            domain: null,
            icon_url: null,
            role: 'admin',
            is_account_owner: true,
          },
          session_id: 'session-journeys-e2e',
          expires_at: '2026-04-12T12:00:00Z',
        }),
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

    if (method === 'GET' && path === '/api/v1/notifications') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/journeys') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          journeys: [],
          total_count: 0,
          page: 1,
          page_size: 50,
        }),
      })
      return
    }

    if (method === 'POST' && path === '/api/v1/notifications/mark-read-all') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ marked: 0 }),
      })
      return
    }

    if (method === 'GET' && path === '/api/v1/journey-definitions') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ definitions: definitions.map(summarizeDefinition) }),
      })
      return
    }

    if (method === 'POST' && path === '/api/v1/journey-definitions') {
      const payload = JSON.parse(request.postData() || '{}')
      const definition = buildDefinition({
        definition_id: 'definition-created',
        slug: payload.slug,
        name: payload.name,
        description: payload.description ?? null,
        subject_strategy: payload.subject_strategy,
        scope: payload.scope,
        tags: payload.tags || [],
        settings: payload.settings || {},
      })
      definitions.unshift(definition)
      versionsByDefinition.set(definition.definition_id, [])
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(definition),
      })
      return
    }

    if (method === 'POST' && path === '/api/v1/journey-definitions/import') {
      const payload = JSON.parse(request.postData() || '{}')
      const entries = Array.isArray(payload.bundle?.definitions) ? payload.bundle.definitions : []
      const importedDefinitionIds: string[] = []
      const importedVersionIds: string[] = []

      entries.forEach((entry: { definition?: Partial<MockDefinition>; versions?: Partial<MockVersion>[] }, index: number) => {
        const importedDefinition = buildDefinition({
          definition_id: entry.definition?.definition_id || `definition-imported-${index + 1}`,
          slug: entry.definition?.slug || `imported-journey-${index + 1}`,
          name: entry.definition?.name || `Imported Journey ${index + 1}`,
          description: entry.definition?.description ?? null,
          subject_strategy: entry.definition?.subject_strategy as MockDefinition['subject_strategy'] | undefined,
          scope: entry.definition?.scope as MockDefinition['scope'] | undefined,
          tags: entry.definition?.tags || [],
          settings: entry.definition?.settings || {},
        })

        const existingIndex = definitions.findIndex((definition) => definition.definition_id === importedDefinition.definition_id)
        if (existingIndex >= 0) {
          definitions.splice(existingIndex, 1)
        }
        definitions.unshift(importedDefinition)
        importedDefinitionIds.push(importedDefinition.definition_id)

        const importedVersions = (entry.versions || []).map((version, versionIndex) =>
          buildVersion({
            definition_version_id: version.definition_version_id || `version-imported-${index + 1}-${versionIndex + 1}`,
            definition_id: importedDefinition.definition_id,
            version_number: version.version_number || versionIndex + 1,
            status: (version.status as 'draft' | 'published' | undefined) || 'draft',
            rules: version.rules || {
              entry_rules: [{ kind: 'conversation_started', metadata: {} }],
              touchpoint_rules: [],
              milestones: [],
              outcome_rules: {},
              abandonment_policy: { close_as: 'abandoned' },
              merge_policy: { reopen_statuses: [] },
            },
            published_at: version.published_at ?? null,
          })
        )

        versionsByDefinition.set(importedDefinition.definition_id, importedVersions)
        importedVersions.forEach((version) => importedVersionIds.push(version.definition_version_id))
      })

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          imported_definition_ids: importedDefinitionIds,
          imported_version_ids: importedVersionIds,
        }),
      })
      return
    }

    const definitionMatch = path.match(/^\/api\/v1\/journey-definitions\/([^/]+)$/)
    if (definitionMatch && method === 'GET') {
      const definition = definitions.find((item) => item.definition_id === definitionMatch[1])
      await route.fulfill({
        status: definition ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(definition || { detail: 'not found' }),
      })
      return
    }

    if (definitionMatch && method === 'PATCH') {
      const payload = JSON.parse(request.postData() || '{}')
      const definition = definitions.find((item) => item.definition_id === definitionMatch[1])
      if (!definition) {
        await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
        return
      }
      Object.assign(definition, {
        ...payload,
        updated_at: '2026-04-11T13:00:00Z',
      })
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(definition),
      })
      return
    }

    const versionsMatch = path.match(/^\/api\/v1\/journey-definitions\/([^/]+)\/versions$/)
    if (versionsMatch && method === 'GET') {
      const versions = versionsByDefinition.get(versionsMatch[1]) || []
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ versions }),
      })
      return
    }

    if (versionsMatch && method === 'POST') {
      const payload = JSON.parse(request.postData() || '{}')
      const definition = definitions.find((item) => item.definition_id === versionsMatch[1])
      if (!definition) {
        await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
        return
      }
      const versions = versionsByDefinition.get(definition.definition_id) || []
      const version = buildVersion({
        definition_version_id: `version-${definition.definition_id}-${versions.length + 1}`,
        definition_id: definition.definition_id,
        version_number: versions.length + 1,
        status: 'draft',
        based_on_version_id: payload.based_on_version_id ?? null,
        rules: payload.rules,
      })
      versions.push(version)
      versionsByDefinition.set(definition.definition_id, versions)
      definition.current_draft_version_id = version.definition_version_id
      definition.updated_at = '2026-04-11T13:10:00Z'

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(version),
      })
      return
    }

    const versionMatch = path.match(/^\/api\/v1\/journey-definition-versions\/([^/]+)$/)
    if (versionMatch && method === 'PATCH') {
      const payload = JSON.parse(request.postData() || '{}')
      let targetVersion: MockVersion | undefined
      for (const versions of versionsByDefinition.values()) {
        const match = versions.find((version) => version.definition_version_id === versionMatch[1])
        if (match) {
          targetVersion = match
          break
        }
      }
      if (!targetVersion) {
        await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
        return
      }
      targetVersion.rules = payload.rules || targetVersion.rules
      targetVersion.updated_at = '2026-04-11T13:20:00Z'
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(targetVersion),
      })
      return
    }

    const reviewMatch = path.match(/^\/api\/v1\/journey-definitions\/([^/]+)\/review$/)
    if (reviewMatch && method === 'GET') {
      const definition = definitions.find((item) => item.definition_id === reviewMatch[1])
      const versions = versionsByDefinition.get(reviewMatch[1]) || []
      await route.fulfill({
        status: definition ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(definition ? buildReadiness(definition, versions) : { detail: 'not found' }),
      })
      return
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    })
  })
}

test('journeys workspace supports create, edit, versioning, and import flows', async ({ page }) => {
  await installJourneyWorkspaceMocks(page)

  await page.goto('/journeys')

  await expect(page.getByRole('heading', { name: 'Definitions', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Existing Journey', exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Create Definition' }).first().click()
  const definitionDialog = page.getByRole('dialog')
  await definitionDialog.getByLabel('Name').fill('Expansion Journey')
  await definitionDialog.getByLabel('Slug').fill('expansion-journey')
  await definitionDialog.getByLabel('Description').fill('Track expansion deals after activation.')
  await definitionDialog.getByRole('button', { name: 'Create Definition' }).click({ force: true })

  await page.getByRole('button', { name: /Expansion Journey/ }).click()
  await expect(page.getByRole('heading', { name: 'Expansion Journey', exact: true })).toBeVisible()
  await expect(page.getByText('Track expansion deals after activation.').first()).toBeVisible()

  await page.getByRole('button', { name: 'Edit' }).click()
  const editDefinitionDialog = page.getByRole('dialog')
  await editDefinitionDialog.getByLabel('Description').fill('Updated expansion journey description.')
  await editDefinitionDialog.getByRole('button', { name: 'Save Definition' }).click({ force: true })

  await expect(page.getByText('Updated expansion journey description.').first()).toBeVisible()

  await page.getByRole('button', { name: 'New Draft' }).click()
  const createVersionDialog = page.getByRole('dialog')
  await createVersionDialog.getByRole('button', { name: 'Create Draft Version' }).click({ force: true })

  await expect(page.getByText('v1', { exact: true })).toBeVisible()
  await expect(page.getByText('draft', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Edit Draft' }).click()
  const editVersionDialog = page.getByRole('dialog')
  await editVersionDialog.getByRole('button', { name: 'Add Milestone' }).click()
  await editVersionDialog.getByLabel('Milestone ID').fill('qualified')
  await editVersionDialog.getByLabel('Milestone Name').fill('Qualified')
  await editVersionDialog.getByLabel('Order Index').fill('1')
  await editVersionDialog.getByLabel('Milestone 1 Enter Rule 1 Value').fill('qualified_state')
  await editVersionDialog.getByRole('button', { name: 'Save Draft Rules' }).click({ force: true })

  await expect(page.getByText('milestones 1')).toBeVisible()

  await page.getByRole('button', { name: 'Import Bundle' }).first().click()
  const importDialog = page.getByRole('dialog')
  await importDialog.getByLabel('Bundle JSON').fill(JSON.stringify({
    schema_version: 'journey_definition_bundle.v1',
    definitions: [
      {
        definition: {
          definition_id: 'definition-imported-manual',
          slug: 'imported-journey',
          name: 'Imported Journey',
          description: 'Imported from another workspace',
          subject_strategy: {
            kind: 'channel_identity',
            value: 'contact',
            fallback_kind: null,
            fallback_value: null,
          },
          scope: {
            agent_ids: [],
            channel_filters: [],
            conversation_mode_filters: [],
          },
          status: 'active',
          tags: ['imported'],
          settings: {},
          current_draft_version_id: null,
          current_published_version_id: null,
          created_by_user_id: null,
          created_at: createdAt,
          updated_at: createdAt,
        },
        versions: [],
      },
    ],
  }, null, 2))
  await importDialog.getByRole('button', { name: 'Import Bundle' }).click({ force: true })

  await page.getByRole('button', { name: /Imported Journey/ }).click()
  await expect(page.getByRole('heading', { name: 'Imported Journey', exact: true })).toBeVisible()
})
