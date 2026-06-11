import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useState,
} from 'react'
import {
  FileText,
  GitBranch,
  Hash,
  ListTree,
  MessageCircle,
  Plus,
  Workflow,
} from 'lucide-react'
import type { Editor, Range } from '@tiptap/core'
import {
  addDirectAnswerInStep,
  addEventHintToStep,
  addSayInStep,
  addStepInScenario,
  addToolBindingToStep,
  addTransitionInStep,
  buildBlankScenario,
} from './commands'

export interface SlashCommand {
  id: string
  label: string
  description: string
  icon: typeof Plus
  // Returns true if this command can run in the current cursor context.
  available: (editor: Editor) => boolean
  run: (editor: Editor, range: Range) => void
}

// Context resolution: walk up the cursor's ancestor chain to figure out
// whether we're at doc-level, inside a scenario, or inside a step.
function ancestorTypeNames(editor: Editor): Set<string> {
  const names = new Set<string>()
  const $pos = editor.state.selection.$from
  for (let depth = $pos.depth; depth >= 0; depth--) {
    names.add($pos.node(depth).type.name)
  }
  return names
}

function findAncestorPos(editor: Editor, typeName: string): number | null {
  const $pos = editor.state.selection.$from
  for (let depth = $pos.depth; depth >= 0; depth--) {
    if ($pos.node(depth).type.name === typeName) return $pos.before(depth)
  }
  return null
}

// Delete the `/foo` trigger range so the typed slash + filter don't end up
// inside the inserted node.
function clearTrigger(editor: Editor, range: Range): void {
  editor.chain().focus().deleteRange(range).run()
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    id: 'scenario',
    label: 'Scenario',
    description: 'Add a new scenario at the end of the document',
    icon: ListTree,
    available: () => true,
    run: (editor, range) => {
      clearTrigger(editor, range)
      const docSize = editor.state.doc.content.size
      editor.chain().focus().insertContentAt(docSize, buildBlankScenario()).run()
    },
  },
  {
    id: 'step',
    label: 'Step',
    description: 'Add a step to the current scenario',
    icon: FileText,
    available: (editor) => ancestorTypeNames(editor).has('scenario'),
    run: (editor, range) => {
      clearTrigger(editor, range)
      const scenarioPos = findAncestorPos(editor, 'scenario')
      if (scenarioPos == null) return
      addStepInScenario(editor, scenarioPos)
    },
  },
  {
    id: 'transition',
    label: 'Transition',
    description: 'Add a transition (otherwise → self by default)',
    icon: GitBranch,
    available: (editor) => ancestorTypeNames(editor).has('step'),
    run: (editor, range) => {
      clearTrigger(editor, range)
      const stepPos = findAncestorPos(editor, 'step')
      if (stepPos == null) return
      addTransitionInStep(editor, stepPos)
    },
  },
  {
    id: 'say',
    label: 'Say',
    description: 'Add an entry-line "say" block to this step',
    icon: MessageCircle,
    available: (editor) => ancestorTypeNames(editor).has('step'),
    run: (editor, range) => {
      clearTrigger(editor, range)
      const stepPos = findAncestorPos(editor, 'step')
      if (stepPos == null) return
      addSayInStep(editor, stepPos)
    },
  },
  {
    id: 'direct',
    label: 'Direct answer',
    description: 'Add a direct-answer prompt block',
    icon: MessageCircle,
    available: (editor) => ancestorTypeNames(editor).has('step'),
    run: (editor, range) => {
      clearTrigger(editor, range)
      const stepPos = findAncestorPos(editor, 'step')
      if (stepPos == null) return
      addDirectAnswerInStep(editor, stepPos)
    },
  },
  {
    id: 'hint',
    label: 'Event hint',
    description: 'Add an intent classifier hint to this step',
    icon: Hash,
    available: (editor) => ancestorTypeNames(editor).has('step'),
    run: (editor, range) => {
      clearTrigger(editor, range)
      const stepPos = findAncestorPos(editor, 'step')
      if (stepPos == null) return
      addEventHintToStep(editor, stepPos)
    },
  },
  {
    id: 'bind-tool',
    label: 'Bind tool…',
    description: 'Allow this step to invoke a Library callable',
    icon: Workflow,
    available: (editor) => ancestorTypeNames(editor).has('step'),
    run: (editor, range) => {
      clearTrigger(editor, range)
      const stepPos = findAncestorPos(editor, 'step')
      if (stepPos == null) return
      addToolBindingToStep(editor, stepPos)
    },
  },
]

export interface SlashMenuListHandle {
  onKeyDown: (event: KeyboardEvent) => boolean
}

interface SlashMenuListProps {
  editor: Editor
  range: Range
  query: string
  onClose: () => void
}

// The popup list. Filters commands by query text and by current context.
export const SlashMenuList = forwardRef<SlashMenuListHandle, SlashMenuListProps>(
  ({ editor, range, query, onClose }, ref) => {
    const [activeIndex, setActiveIndex] = useState(0)
    const lower = query.trim().toLowerCase()
    const items = SLASH_COMMANDS.filter((cmd) => {
      if (!cmd.available(editor)) return false
      if (!lower) return true
      return cmd.label.toLowerCase().includes(lower) || cmd.id.includes(lower)
    })

    useEffect(() => {
      setActiveIndex(0)
    }, [query])

    const select = (index: number) => {
      const cmd = items[index]
      if (!cmd) return
      cmd.run(editor, range)
      onClose()
    }

    useImperativeHandle(ref, () => ({
      onKeyDown: (event: KeyboardEvent) => {
        if (event.key === 'ArrowDown') {
          setActiveIndex((current) => (current + 1) % Math.max(items.length, 1))
          return true
        }
        if (event.key === 'ArrowUp') {
          setActiveIndex((current) => (current - 1 + items.length) % Math.max(items.length, 1))
          return true
        }
        if (event.key === 'Enter') {
          select(activeIndex)
          return true
        }
        return false
      },
    }), [activeIndex, items, range])

    if (items.length === 0) {
      return (
        <div className="doc-slash-menu">
          <div className="doc-slash-menu-empty">No matching commands</div>
        </div>
      )
    }

    return (
      <div className="doc-slash-menu">
        {items.map((cmd, index) => (
          <button
            key={cmd.id}
            type="button"
            onClick={() => select(index)}
            onMouseEnter={() => setActiveIndex(index)}
            className={`doc-slash-menu-item ${
              index === activeIndex ? 'doc-slash-menu-item-active' : ''
            }`}
          >
            <cmd.icon className="h-3.5 w-3.5 shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-foreground">{cmd.label}</div>
              <div className="truncate text-[11px] text-muted-foreground">
                {cmd.description}
              </div>
            </div>
          </button>
        ))}
      </div>
    )
  },
)

SlashMenuList.displayName = 'SlashMenuList'
