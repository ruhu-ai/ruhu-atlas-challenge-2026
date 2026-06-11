// Mirror of ``ruhu.tools.executors.code.resolve_callable_aliases`` so the
// Library UI can show the exact sandbox identifier the author needs to
// type in their code body. The two implementations MUST stay byte-equal
// in their resolution logic — if you change one, change the other and
// keep both test suites in sync.

const VALID_IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/

function isPythonIdentifier(name: string): boolean {
  // Mirrors ``str.isidentifier()`` for the ASCII subset we care about.
  // The Python sandbox additionally forbids leading underscores via
  // RestrictedPython; that check lives in the caller.
  return VALID_IDENT.test(name)
}

/**
 * Build a deterministic ``{alias: ref}`` map for a Code-kind callable's
 * ``callable_refs``. Author can pin specific aliases via ``explicit``
 * (typically when two refs would otherwise collide on their last
 * dot-segment). Unpinned refs default to the segment after the last
 * ``.``; on collision, fall back to the underscored full ref. Last-resort
 * disambiguation uses a numeric suffix.
 */
export function resolveCallableAliases(
  refs: string[],
  explicit: Record<string, string> = {},
): Record<string, string> {
  const out: Record<string, string> = { ...explicit }
  const used = new Set(Object.keys(explicit))
  const pinned = new Set(Object.values(explicit))

  for (const ref of refs) {
    if (pinned.has(ref)) continue
    const lastSegment = ref.split('.').pop() || ref
    let candidate: string | null
    if (
      isPythonIdentifier(lastSegment)
      && !lastSegment.startsWith('_')
      && !used.has(lastSegment)
    ) {
      candidate = lastSegment
    } else {
      candidate = ref.replace(/[.-]/g, '_')
    }
    if (used.has(candidate)) {
      const base = candidate
      let counter = 2
      while (used.has(candidate)) {
        candidate = `${base}_${counter}`
        counter += 1
      }
    }
    out[candidate] = ref
    used.add(candidate)
  }
  return out
}
