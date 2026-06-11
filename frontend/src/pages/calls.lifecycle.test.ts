import { classifyLifecycle } from './calls.lifecycle'

describe('classifyLifecycle', () => {
  it('classifies grounding.updated as grounding', () => {
    expect(classifyLifecycle('grounding', 'updated')).toBe('grounding')
  })

  it('classifies artifact generic names by family', () => {
    expect(classifyLifecycle('artifact', 'created')).toBe('artifact')
    expect(classifyLifecycle('artifact', 'resolution_ambiguous')).toBe('artifact')
  })

  it('still classifies interaction lifecycle names by event name', () => {
    expect(classifyLifecycle('interaction', 'activity_started')).toBe('activity')
    expect(classifyLifecycle('interaction', 'permission_requested')).toBe('permission')
  })
})
