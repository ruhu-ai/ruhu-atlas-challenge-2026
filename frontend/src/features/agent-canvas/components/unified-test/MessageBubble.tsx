import { useEffect, useRef, useState } from 'react'
import { Mic, Paperclip } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TranscriptEntry } from './types'

function useWordReveal(text: string, active: boolean, onComplete: () => void) {
  const words = text.split(' ')
  const [count, setCount] = useState(active ? 0 : words.length)
  const activeRef = useRef(active)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  useEffect(() => {
    activeRef.current = active
    if (!active) {
      setCount(words.length)
      return
    }
    setCount(0)
    let current = 0
    const id = setInterval(() => {
      current += 1
      setCount(current)
      if (current >= words.length) {
        clearInterval(id)
        onCompleteRef.current()
      }
    }, 40)
    return () => clearInterval(id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, text])

  return active ? words.slice(0, count).join(' ') : text
}

export function MessageBubble({
  entry,
  onStreamingComplete,
}: {
  entry: TranscriptEntry
  onStreamingComplete: (id: string) => void
}) {
  const isUser = entry.speaker === 'user'
  const displayText = useWordReveal(
    entry.text,
    Boolean(entry.isStreaming),
    () => onStreamingComplete(entry.id),
  )

  return (
    <div className={cn('flex w-full', isUser ? 'justify-end' : 'justify-start')}>
      <div className={cn('max-w-[85%] flex flex-col', isUser ? 'items-end' : 'items-start')}>
        {(entry.text || entry.isPartial) && (
          <div
            className={cn(
              'text-sm leading-relaxed',
              isUser
                ? 'bg-muted text-foreground rounded-2xl rounded-br-sm px-3.5 py-2'
                : 'text-foreground'
            )}
          >
            {displayText}
            {entry.isPartial && (
              <span className="inline-block w-[2px] h-[1em] ml-0.5 align-text-bottom bg-current opacity-60 animate-pulse" />
            )}
          </div>
        )}
        {entry.attachments && entry.attachments.length > 0 && (
          <div className={cn('flex flex-wrap gap-1.5 mt-1', isUser ? 'justify-end' : 'justify-start')}>
            {entry.attachments.map((att) => (
              <div
                key={att.attachmentId}
                className={cn(
                  'flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs',
                  isUser
                    ? 'bg-muted text-foreground'
                    : 'text-muted-foreground'
                )}
              >
                <Paperclip className="h-3 w-3 shrink-0" />
                <span className="truncate max-w-[160px]">{att.filename}</span>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center gap-1 mt-0.5 px-1">
          {entry.source === 'voice' && (
            <Mic className="h-2.5 w-2.5 text-muted-foreground" />
          )}
          <span className="text-[10px] text-muted-foreground">
            {entry.timestamp.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })}
          </span>
        </div>
      </div>
    </div>
  )
}
