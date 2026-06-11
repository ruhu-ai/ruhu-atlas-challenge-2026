import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Input } from '@/components/atoms/input'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/atoms/popover'
import { toolService } from '@/api/services/tools.service'
import type { ExternalToolCatalogItem } from '@/api/services/tools.service'
import { cn } from '@/lib/utils'

interface ToolRefComboboxProps {
  value: string
  onChange: (value: string) => void
  agentId: string | null
  placeholder?: string
  className?: string
}

export function ToolRefCombobox({
  value,
  onChange,
  agentId,
  placeholder = 'e.g. knowledge.lookup, crm.create_contact',
  className,
}: ToolRefComboboxProps) {
  const [open, setOpen] = useState(false)
  const [highlightIndex, setHighlightIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const { data: catalog } = useQuery({
    queryKey: ['tool-catalog', agentId],
    queryFn: () => toolService.getCatalog(agentId!),
    enabled: !!agentId,
    staleTime: 60_000,
  })

  const filtered = useMemo(() => {
    if (!catalog || !Array.isArray(catalog)) return []
    if (!value.trim()) return catalog
    const q = value.toLowerCase()
    return catalog.filter(
      (item) =>
        item.ref.toLowerCase().includes(q) ||
        item.display_name.toLowerCase().includes(q) ||
        item.description.toLowerCase().includes(q),
    )
  }, [catalog, value])

  // Reset highlight when filtered list changes
  useEffect(() => {
    setHighlightIndex(0)
  }, [filtered.length])

  // Scroll the highlighted item into view
  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.children[highlightIndex] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [highlightIndex, open])

  const selectItem = (item: ExternalToolCatalogItem) => {
    onChange(item.ref)
    setOpen(false)
    inputRef.current?.blur()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open || filtered.length === 0) return

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setHighlightIndex((prev) => Math.min(prev + 1, filtered.length - 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setHighlightIndex((prev) => Math.max(prev - 1, 0))
        break
      case 'Enter':
        e.preventDefault()
        if (filtered[highlightIndex]) selectItem(filtered[highlightIndex])
        break
      case 'Escape':
        e.preventDefault()
        setOpen(false)
        break
    }
  }

  const showDropdown = open && !!agentId && filtered.length > 0

  return (
    <Popover open={showDropdown} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <Input
          ref={inputRef}
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
            if (!open) setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className={cn('font-mono text-xs', className)}
          autoComplete="off"
        />
      </PopoverAnchor>
      {showDropdown && (
        <PopoverContent
          className="max-h-56 w-[var(--radix-popover-trigger-width)] overflow-y-auto p-1"
          align="start"
          sideOffset={4}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={() => setOpen(false)}
        >
          <div ref={listRef}>
            {filtered.map((item, idx) => (
              <button
                key={item.ref}
                type="button"
                className={cn(
                  'flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left',
                  idx === highlightIndex
                    ? 'bg-accent text-accent-foreground'
                    : 'hover:bg-accent/50',
                )}
                onMouseEnter={() => setHighlightIndex(idx)}
                onMouseDown={(e) => {
                  e.preventDefault() // prevent blur before click registers
                  selectItem(item)
                }}
              >
                <span className="font-mono text-xs">{item.ref}</span>
                {item.display_name && item.display_name !== item.ref && (
                  <span className="text-[10px] text-muted-foreground">
                    {item.display_name}
                    {item.description ? ` — ${item.description.slice(0, 60)}` : ''}
                  </span>
                )}
              </button>
            ))}
          </div>
        </PopoverContent>
      )}
    </Popover>
  )
}
