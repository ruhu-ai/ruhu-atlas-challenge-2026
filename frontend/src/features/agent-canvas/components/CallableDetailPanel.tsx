/**
 * Detail page for a selected Library callable.
 *
 * Extracted from LibraryView.tsx (RP-4.4) — header (inline-editable name,
 * badges, duplicate/delete actions with the usage-aware confirm) plus the
 * kind-specific tab set. All draft state and mutations live in
 * useCallableDetailEditor; tab bodies live in CallableDetailTabs and
 * CompositeEditors.
 */
import { Suspense, lazy } from 'react'
import {
  ArrowLeft,
  Check,
  Copy,
  Loader2,
  Pencil,
  Save,
  Shield,
  Trash2,
} from 'lucide-react'

import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/atoms/alert-dialog'
import { Card, CardContent, CardHeader } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import { Textarea } from '@/components/atoms/textarea'
import { cn } from '@/lib/utils'
import type { CallableUsageRef } from '@/features/agent-canvas/hooks/useCallableUsageIndex'
import { useCallableDetailEditor } from '@/features/agent-canvas/hooks/useCallableDetailEditor'

import {
  ApisTab,
  BuiltinSummary,
  CallableFunctionsTab,
  CalledBySection,
  ConnectionSummary,
  VariableUsageTab,
} from './CallableDetailTabs'
import { CompositeStepsEditor, OutputMappingEditor } from './CompositeEditors'
import {
  KIND_BADGE_VARIANT,
  KIND_ICON,
  KIND_LABEL,
  TABS_BY_KIND,
  extractFactReferences,
  type CallableEntry,
} from './library-view-helpers'

// Monaco is heavy — lazy-load only when a Code-kind callable is opened.
const CodeEditor = lazy(() =>
  import('@/components/molecules/code-editor').then((m) => ({ default: m.CodeEditor })),
)

export function CallableDetailPanel({
  entry,
  onClose,
  usageRefs,
  usageLoading,
  usageReady,
}: {
  entry: CallableEntry
  onClose: () => void
  usageRefs: CallableUsageRef[]
  usageLoading: boolean
  usageReady: boolean
}) {
  const Icon = KIND_ICON[entry.kind]
  const tool = entry.raw

  const {
    httpMethod, setHttpMethod, endpointPath, setEndpointPath,
    timeoutMs, setTimeoutMs, readOnly, setReadOnly,
    schemaText, setSchemaText, schemaError, setSchemaError,
    isEditingName, setIsEditingName, draftName, setDraftName,
    callableRefs, setCallableRefs, codeBody, setCodeBody,
    compositeSteps, setCompositeSteps, outputMapping, setOutputMapping,
    httpDirty, schemaDirty, codeDirty, compositeDirty,
    outputMappingDirty, callableRefsDirty,
    deleteDialogOpen, setDeleteDialogOpen,
    updateMutation, deleteMutation, duplicateMutation,
    saveHttp, saveSchema, saveCode, saveName,
    saveCallableRefs, saveComposite, saveOutputMapping,
  } = useCallableDetailEditor(entry, onClose)

  // Framework- and provider-templated callables are not editable through
  // the Library UI: built-ins ship with the runtime, MCP tools come from
  // the registered server, integration tools are templated when the
  // provider is connected, and ``read_only`` tools have been pinned by
  // the org admin. The detail page hides Edit/Delete affordances for all
  // of them; authors customise by duplicating to a new Code callable.
  const isFrameworkManaged =
    entry.kind === 'builtin_tool'
    || entry.kind === 'mcp_tool'
    || entry.kind === 'reference_tool'
    || entry.kind === 'integration_tool'
    || tool.read_only
  const usageCount = usageRefs.length

  // Tabs are kind-specific (per the design review): each kind shows
  // ONLY the tabs that apply, no greyed-out placeholders. The default
  // tab is the first entry in the kind's tab set.
  const tabs = TABS_BY_KIND[entry.kind] ?? [{ id: 'used' as const, label: 'Used by' }]
  const defaultTab = tabs[0]?.id ?? 'used'

  return (
    <div className="space-y-4">
      {/* Back arrow + action buttons. Built-in / MCP / reference tools
          are framework-managed — no Edit / Delete affordance. The
          delete confirm is usage-aware: when steps still reference the
          tool, the dialog warns up front and the destructive button
          stays explicit. */}
      <div className="flex items-center justify-between gap-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClose}
          className="-ml-2 gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Library
        </Button>
        <div className="flex items-center gap-2">
          {!isFrameworkManaged && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => duplicateMutation.mutate()}
              disabled={duplicateMutation.isPending}
            >
              {duplicateMutation.isPending ? (
                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Copy className="mr-2 h-3.5 w-3.5" />
              )}
              Duplicate
            </Button>
          )}
          {!isFrameworkManaged && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDeleteDialogOpen(true)}
              disabled={deleteMutation.isPending}
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="mr-2 h-3.5 w-3.5" />
              Delete
            </Button>
          )}
        </div>
      </div>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{entry.displayName}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <span className="block">
                This removes the callable from the Library. Existing
                trace records that referenced it stay intact, but new
                turns can no longer invoke it.
              </span>
              {usageCount > 0 && (
                <span className="block rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-destructive">
                  <strong>Used by {usageCount} step{usageCount === 1 ? '' : 's'}.</strong>{' '}
                  Those bindings will break until the steps are
                  re-pointed to another callable.
                </span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                deleteMutation.mutate()
              }}
              disabled={deleteMutation.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
              ) : null}
              Delete callable
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Card className="flex flex-col overflow-hidden">
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 border-b border-border/60 pb-4">
          <div className="flex min-w-0 items-start gap-3">
            <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-border bg-muted/40 text-muted-foreground" aria-hidden>
              <Icon className="h-5 w-5" />
            </span>
            <div className="min-w-0 space-y-1.5">
              {isEditingName && !isFrameworkManaged ? (
                <div className="flex items-center gap-1.5">
                  <Input
                    aria-label="Display name"
                    value={draftName}
                    autoFocus
                    onChange={(event) => setDraftName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        saveName()
                      } else if (event.key === 'Escape') {
                        event.preventDefault()
                        setIsEditingName(false)
                        setDraftName(entry.displayName)
                      }
                    }}
                    onBlur={saveName}
                    className="h-7 max-w-[24rem] text-base font-semibold"
                  />
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label="Save display name"
                    onMouseDown={(event) => {
                      // onMouseDown so the click fires before the input's onBlur
                      // discards the draft.
                      event.preventDefault()
                      saveName()
                    }}
                  >
                    <Check className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    if (isFrameworkManaged) return
                    setIsEditingName(true)
                  }}
                  className={cn(
                    'group flex max-w-full items-center gap-1.5 truncate text-left text-base font-semibold',
                    !isFrameworkManaged && 'hover:text-primary',
                  )}
                  aria-label={isFrameworkManaged ? entry.displayName : 'Edit display name'}
                  disabled={isFrameworkManaged}
                >
                  <h3 className="truncate">{entry.displayName}</h3>
                  {!isFrameworkManaged && (
                    <Pencil className="h-3.5 w-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                  )}
                </button>
              )}
              <code className="block truncate rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                {entry.name}
              </code>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant={KIND_BADGE_VARIANT[entry.kind]} className="text-[10px]">
                  {KIND_LABEL[entry.kind]}
                </Badge>
                <Badge variant="outline" className="text-[10px]">v{entry.version}</Badge>
                {entry.deprecated && (
                  <Badge variant="outline" className="text-[10px] text-muted-foreground">Deprecated</Badge>
                )}
                {!entry.isActive && !entry.deprecated && (
                  <Badge variant="outline" className="text-[10px] text-muted-foreground">Inactive</Badge>
                )}
                {tool.read_only && (
                  <Badge variant="outline" className="gap-1 text-[10px]">
                    <Shield className="h-2.5 w-2.5" />
                    Read-only
                  </Badge>
                )}
              </div>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4 pt-4 text-sm">
          {entry.description && (
            <p className="text-sm text-muted-foreground">{entry.description}</p>
          )}

          <Tabs defaultValue={defaultTab} className="w-full">
            <TabsList className="h-auto flex-wrap justify-start gap-1 bg-transparent p-0">
              {tabs.map((tab) => (
                <TabsTrigger
                  key={tab.id}
                  value={tab.id}
                  className="h-8 rounded-md px-3 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
                >
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>

            {/* Code tab — Code-kind only (kind-specific TabsList ensures
                this trigger is hidden for other kinds). */}
            <TabsContent value="code" className="space-y-2 pt-3">
              <Label className="text-[11px] text-muted-foreground" htmlFor="callable-code-body">
                Python body — runs in the RestrictedPython sandbox.
                Read inputs via <code className="rounded bg-muted px-1">vars[&quot;key&quot;]</code>.
                Set <code className="rounded bg-muted px-1">result = {'{...}'}</code> to return.
              </Label>
              <Suspense
                fallback={
                  <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
                    <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                    Loading editor…
                  </div>
                }
              >
                <CodeEditor
                  value={codeBody}
                  onChange={(value) => setCodeBody(value)}
                  language="python"
                  height="320px"
                  placeholder="# vars and variables expose the call args; set result to return."
                />
              </Suspense>
              <Button
                size="sm"
                onClick={saveCode}
                disabled={!codeDirty || updateMutation.isPending}
              >
                {updateMutation.isPending ? (
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                ) : (
                  <Save className="mr-2 h-3 w-3" />
                )}
                Save code
              </Button>
            </TabsContent>

            {/* Request tab — API / OpenAPI only: HTTP method, path,
                timeout, idempotency. Kind-specific TabsList ensures
                this trigger is hidden for non-API kinds. */}
            <TabsContent value="request" className="space-y-3 pt-3">
              <div className="grid grid-cols-[6rem_1fr] gap-2">
                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground" htmlFor="callable-http-method">Method</Label>
                  <Select value={httpMethod} onValueChange={setHttpMethod}>
                    <SelectTrigger id="callable-http-method" className="h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map((m) => (
                        <SelectItem key={m} value={m}>{m}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground" htmlFor="callable-endpoint-path">Path</Label>
                  <Input
                    id="callable-endpoint-path"
                    value={endpointPath}
                    onChange={(e) => setEndpointPath(e.target.value)}
                    placeholder="/v1/users/{user_id}"
                    className="h-8 font-mono text-xs"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground" htmlFor="callable-timeout-ms">Timeout (ms)</Label>
                  <Input
                    id="callable-timeout-ms"
                    type="number"
                    value={timeoutMs}
                    min={500}
                    max={120000}
                    onChange={(e) => setTimeoutMs(Number(e.target.value) || 5000)}
                    className="h-8 text-xs"
                  />
                </div>
                <label className="flex items-end gap-2 pb-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={readOnly}
                    onChange={(e) => setReadOnly(e.target.checked)}
                    className="h-3 w-3"
                  />
                  Safe for autonomous use
                </label>
              </div>
              {entry.connection && (
                <p className="text-[11px] text-muted-foreground">
                  via {entry.connection.name} ({entry.connection.slug})
                </p>
              )}
              <Button
                size="sm"
                onClick={saveHttp}
                disabled={!httpDirty || updateMutation.isPending}
              >
                {updateMutation.isPending ? (
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                ) : (
                  <Save className="mr-2 h-3 w-3" />
                )}
                Save request config
              </Button>
            </TabsContent>

            {/* Schema tab — JSON editor + output mapping */}
            <TabsContent value="schema" className="space-y-4 pt-3">
              <div className="space-y-2">
                <Label className="text-[11px] text-muted-foreground" htmlFor="callable-schema">
                  Input schema (JSON)
                </Label>
                <Textarea
                  id="callable-schema"
                  value={schemaText}
                  onChange={(e) => {
                    setSchemaText(e.target.value)
                    setSchemaError(null)
                  }}
                  rows={10}
                  className="font-mono text-[11px]"
                  spellCheck={false}
                />
                {schemaError && (
                  <p className="text-[11px] text-destructive">Invalid JSON: {schemaError}</p>
                )}
                <Button
                  size="sm"
                  onClick={saveSchema}
                  disabled={!schemaDirty || updateMutation.isPending}
                  className="w-full"
                >
                  {updateMutation.isPending ? (
                    <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                  ) : (
                    <Save className="mr-2 h-3 w-3" />
                  )}
                  Save schema
                </Button>
              </div>

            </TabsContent>

            {/* Calls (outbound) — Composite-kind only: edit the chain of
                callables that compose this tool. Code-kind bodies make
                their own calls inline (so this tab isn't shown for code). */}
            <TabsContent value="calls" className="space-y-2 pt-3">
              <CompositeStepsEditor
                steps={compositeSteps}
                onChange={setCompositeSteps}
                dirty={compositeDirty}
                saving={updateMutation.isPending}
                onSave={saveComposite}
              />
            </TabsContent>

            {/* Output — Composite-kind only: declare how the chained
                steps' final output maps into facts. Code-kind exposes
                this same machinery via Global Variables; APIs use
                connection-templated output. */}
            <TabsContent value="output" className="pt-3">
              <OutputMappingEditor
                mapping={outputMapping}
                onChange={setOutputMapping}
                dirty={outputMappingDirty}
                saving={updateMutation.isPending}
                onSave={saveOutputMapping}
              />
            </TabsContent>

            {/* Callable functions — Code-kind only: which built-in
                helpers + custom function definitions the code body can
                call (Code/Composite/Built-in callables). The runtime
                computes a deterministic alias for each ref so the body
                can invoke them by short name; the UI shows that alias
                next to each ref. */}
            <TabsContent value="callables" className="pt-3">
              <CallableFunctionsTab
                ownRef={tool.tool_ref}
                selectedRefs={callableRefs}
                onToggle={(ref) => {
                  setCallableRefs((current) =>
                    current.includes(ref)
                      ? current.filter((r) => r !== ref)
                      : [...current, ref],
                  )
                }}
                explicitAliases={
                  (tool.metadata?.callable_aliases ?? {}) as Record<string, string>
                }
                dirty={callableRefsDirty}
                saving={updateMutation.isPending}
                onSave={saveCallableRefs}
              />
            </TabsContent>

            {/* APIs — Code-kind only: which Library API/integration tools
                this code body is permitted to invoke. Same backing field
                (``metadata.callable_refs``) as Callable functions; UI
                groups by connection so authors can scan by provider. */}
            <TabsContent value="apis" className="pt-3">
              <ApisTab
                ownRef={tool.tool_ref}
                selectedRefs={callableRefs}
                onToggle={(ref) => {
                  setCallableRefs((current) =>
                    current.includes(ref)
                      ? current.filter((r) => r !== ref)
                      : [...current, ref],
                  )
                }}
                explicitAliases={
                  (tool.metadata?.callable_aliases ?? {}) as Record<string, string>
                }
                dirty={callableRefsDirty}
                saving={updateMutation.isPending}
                onSave={saveCallableRefs}
              />
            </TabsContent>

            {/* Global Variables — Code-kind only: read/write cross-ref.
                Reads come from $facts.<name> tokens in bound args
                across every step that calls this callable; writes
                come from the declared output_mapping on this tool. */}
            <TabsContent value="vars" className="pt-3">
              <VariableUsageTab
                references={extractFactReferences(usageRefs)}
                writes={outputMapping}
              />
            </TabsContent>

            {/* Connection — API-kind only: which api_connection (host
                + credentials) backs this tool. Read-only here; manage
                the connection itself in the Connections tab. */}
            <TabsContent value="connection" className="pt-3">
              <ConnectionSummary tool={tool} />
            </TabsContent>

            {/* Summary — Built-in / MCP / reference: read-only metadata.
                These tools are framework- or protocol-managed; authors
                cannot edit their behaviour from the Library. */}
            <TabsContent value="summary" className="pt-3">
              <BuiltinSummary entry={entry} tool={tool} />
            </TabsContent>

            {/* Used by — backref. Same content for every kind. */}
            <TabsContent value="used" className="pt-3">
              <CalledBySection
                refs={usageRefs}
                loading={usageLoading}
                ready={usageReady}
              />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}
