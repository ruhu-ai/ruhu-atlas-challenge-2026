/**
 * Mirrors ``tests/test_tools_code_composite.py`` resolve_callable_aliases
 * cases — keep these in sync. If the backend's resolution logic changes,
 * both suites must be updated together so the alias the UI shows matches
 * the alias the executor binds.
 */
import { resolveCallableAliases } from './callable-aliases'

describe('resolveCallableAliases', () => {
  it('falls back to last segment when unique', () => {
    const out = resolveCallableAliases([
      'crm.get_user',
      'banking.create_account',
    ])
    expect(out).toEqual({
      get_user: 'crm.get_user',
      create_account: 'banking.create_account',
    })
  })

  it('disambiguates colliding last-segments by underscoring the full ref', () => {
    const out = resolveCallableAliases(['crm.get_user', 'banking.get_user'])
    // First wins the short name; second falls back to underscored form.
    expect(out.get_user).toBe('crm.get_user')
    expect(out.banking_get_user).toBe('banking.get_user')
  })

  it('respects explicit pins for one ref and assigns the colliding one a fallback', () => {
    const out = resolveCallableAliases(
      ['crm.get_user', 'banking.get_user'],
      { get_user: 'banking.get_user' },
    )
    expect(out.get_user).toBe('banking.get_user')
    expect(out.crm_get_user).toBe('crm.get_user')
  })

  it('numeric-suffixes when both short and underscored forms are pinned', () => {
    // Pin both the short ``act`` alias AND the underscored full ref
    // ``ns_act`` to a different ref. Adding a fresh ``ns.act`` ref then
    // exercises the numeric-suffix path because every cheaper candidate
    // is already used.
    const out = resolveCallableAliases(
      ['ns.act'],
      {
        act: 'other.act',
        ns_act: 'third.ns_act',
      },
    )
    expect(out.act).toBe('other.act')
    expect(out.ns_act).toBe('third.ns_act')
    expect(out.ns_act_2).toBe('ns.act')
  })

  it('handles refs whose last segment is not a valid Python identifier', () => {
    const out = resolveCallableAliases(['ns.123bad'])
    // ``123bad`` fails isidentifier — falls back to underscored full ref.
    expect(out.ns_123bad).toBe('ns.123bad')
  })

  it('returns empty map for empty input', () => {
    expect(resolveCallableAliases([])).toEqual({})
  })
})
