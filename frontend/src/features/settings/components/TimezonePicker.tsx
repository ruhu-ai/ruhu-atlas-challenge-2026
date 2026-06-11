import { useMemo, useRef, useState } from 'react'
import { Button } from '@/components/atoms/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/atoms/popover'
import { Check, ChevronsUpDown, Globe, Search } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Curated high-volume IANA timezones shown at the top of the picker. */
export const CURATED_TIMEZONES: { value: string; label: string; group: string }[] = [
  { value: 'UTC', label: 'UTC', group: 'Common' },
  { value: 'America/New_York', label: 'Eastern Time (New York)', group: 'Common' },
  { value: 'America/Chicago', label: 'Central Time (Chicago)', group: 'Common' },
  { value: 'America/Denver', label: 'Mountain Time (Denver)', group: 'Common' },
  { value: 'America/Los_Angeles', label: 'Pacific Time (Los Angeles)', group: 'Common' },
  { value: 'Europe/London', label: 'London (GMT)', group: 'Europe' },
  { value: 'Europe/Paris', label: 'Paris (CET)', group: 'Europe' },
  { value: 'Europe/Berlin', label: 'Berlin (CET)', group: 'Europe' },
  { value: 'Asia/Dubai', label: 'Dubai (GST)', group: 'Middle East' },
  { value: 'Asia/Riyadh', label: 'Riyadh (AST)', group: 'Middle East' },
  { value: 'Asia/Kolkata', label: 'Mumbai / Kolkata (IST)', group: 'Asia / Pacific' },
  { value: 'Asia/Tokyo', label: 'Tokyo (JST)', group: 'Asia / Pacific' },
  { value: 'Asia/Shanghai', label: 'Shanghai (CST)', group: 'Asia / Pacific' },
  { value: 'Australia/Sydney', label: 'Sydney (AEST)', group: 'Asia / Pacific' },
  { value: 'Pacific/Auckland', label: 'Auckland (NZST)', group: 'Asia / Pacific' },
  { value: 'Africa/Cairo', label: 'Cairo (EET)', group: 'Africa' },
  { value: 'Africa/Lagos', label: 'Lagos (WAT)', group: 'Africa' },
  { value: 'Africa/Nairobi', label: 'Nairobi (EAT)', group: 'Africa' },
]

const CURATED_SET = new Set(CURATED_TIMEZONES.map((tz) => tz.value))

/** Format an IANA timezone identifier into a display label with GMT offset. */
export function formatTimezoneLabel(tz: string): string {
  try {
    const now = new Date()
    const offset = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'shortOffset' })
      .formatToParts(now)
      .find((p) => p.type === 'timeZoneName')?.value ?? ''
    const city = tz.split('/').pop()?.replace(/_/g, ' ') ?? tz
    return `(${offset}) ${city}`
  } catch {
    return tz
  }
}

/** Build the full IANA timezone list (excluding curated ones), lazily computed. */
function buildAllTimezones(): { value: string; label: string }[] {
  try {
    const allZones = (Intl as any).supportedValuesOf('timeZone') as string[]
    return allZones
      .filter((tz: string) => !CURATED_SET.has(tz))
      .map((tz: string) => ({ value: tz, label: formatTimezoneLabel(tz) }))
  } catch {
    return []
  }
}

/** Searchable timezone picker using Popover. */
export function TimezonePicker({
  value,
  onChange,
}: {
  value: string
  onChange: (tz: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const allTimezones = useMemo(buildAllTimezones, [])

  const lowerSearch = search.toLowerCase()

  const filteredCurated = useMemo(() => CURATED_TIMEZONES.filter(
    (tz) => tz.value.toLowerCase().includes(lowerSearch) || tz.label.toLowerCase().includes(lowerSearch)
  ), [lowerSearch])
  const filteredAll = useMemo(() => allTimezones.filter(
    (tz) => tz.value.toLowerCase().includes(lowerSearch) || tz.label.toLowerCase().includes(lowerSearch)
  ), [lowerSearch, allTimezones])

  const selectedLabel = CURATED_TIMEZONES.find((tz) => tz.value === value)?.label
    ?? formatTimezoneLabel(value)

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setTimeout(() => inputRef.current?.focus(), 0) }}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between font-normal"
        >
          <span className="flex items-center gap-2 truncate">
            <Globe className="h-4 w-4 shrink-0 text-muted-foreground" />
            {selectedLabel}
          </span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[340px] p-0" align="start">
        <div className="flex items-center border-b px-3">
          <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
          <input
            ref={inputRef}
            placeholder="Search timezones..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex h-10 w-full bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {filteredCurated.length > 0 && (
            <>
              <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">Popular</div>
              {filteredCurated.map((tz) => (
                <button
                  key={tz.value}
                  type="button"
                  onClick={() => { onChange(tz.value); setOpen(false); setSearch('') }}
                  className={cn(
                    'relative flex w-full cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-accent hover:text-accent-foreground',
                    value === tz.value && 'bg-accent'
                  )}
                >
                  <Check className={cn('mr-2 h-4 w-4', value === tz.value ? 'opacity-100' : 'opacity-0')} />
                  {tz.label}
                </button>
              ))}
            </>
          )}
          {filteredAll.length > 0 && (
            <>
              <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground mt-1">All Timezones</div>
              {filteredAll.map((tz) => (
                <button
                  key={tz.value}
                  type="button"
                  onClick={() => { onChange(tz.value); setOpen(false); setSearch('') }}
                  className={cn(
                    'relative flex w-full cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-accent hover:text-accent-foreground',
                    value === tz.value && 'bg-accent'
                  )}
                >
                  <Check className={cn('mr-2 h-4 w-4', value === tz.value ? 'opacity-100' : 'opacity-0')} />
                  {tz.label}
                </button>
              ))}
            </>
          )}
          {filteredCurated.length === 0 && filteredAll.length === 0 && (
            <div className="px-2 py-6 text-center text-sm text-muted-foreground">No timezones found.</div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
