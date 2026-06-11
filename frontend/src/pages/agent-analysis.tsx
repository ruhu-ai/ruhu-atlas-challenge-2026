/**
 * Agent Analysis Page (/agents/:id/analysis)
 *
 * Authoring surface for the agent's analysis_schema — reportable variables
 * extracted post-call. Each variable becomes a citation with the same
 * confidence + utterance grounding as a turn-time capture.
 */

import React, { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Plus, Trash2, Save, Tag } from 'lucide-react'

import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { apiClient } from '@/api/client'
import type {
  AgentDocument,
  AnalysisVariableDef,
  AnalysisVariableType,
} from '@/types/agent-document'

interface AgentDocumentResponse {
  agent_id: string
  target: string
  document: AgentDocument
}

const TYPE_OPTIONS: AnalysisVariableType[] = ['string', 'number', 'boolean', 'category', 'array']

function blankVariable(): AnalysisVariableDef {
  return {
    name: '',
    type: 'string',
    description: '',
    source: 'transcript',
  }
}

function parseCategories(input: string): string[] {
  return input
    .split(',')
    .map((token) => token.trim())
    .filter((token) => token.length > 0)
}

function categoriesToString(categories: string[] | null | undefined): string {
  return (categories ?? []).join(', ')
}

function validateSchema(schema: AnalysisVariableDef[]): string | null {
  const seen = new Set<string>()
  for (const variable of schema) {
    const name = variable.name.trim()
    if (!name) return 'Every variable needs a name.'
    if (seen.has(name)) return `Variable "${name}" is duplicated.`
    seen.add(name)
    if (variable.type === 'category' && (variable.categories ?? []).length === 0) {
      return `Variable "${name}" is a category but has no categories listed.`
    }
    if (variable.type !== 'category' && (variable.categories ?? []).length > 0) {
      return `Variable "${name}" has categories but isn't a category type.`
    }
  }
  return null
}

export default function AgentAnalysisPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [schema, setSchema] = useState<AnalysisVariableDef[]>([])
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  const documentQuery = useQuery({
    queryKey: ['agent-document', id, 'draft'],
    queryFn: () =>
      apiClient.get<AgentDocumentResponse>(`/agents/${id}/agent-document`, {
        params: { target: 'draft' },
      }),
    enabled: Boolean(id),
  })

  useEffect(() => {
    const loadedSchema = documentQuery.data?.document?.analysis_schema
    if (loadedSchema) {
      setSchema(loadedSchema)
    } else if (documentQuery.data?.document) {
      setSchema([])
    }
  }, [documentQuery.data])

  const saveMutation = useMutation({
    mutationFn: async (next: AnalysisVariableDef[]) => {
      if (!id) throw new Error('missing agent id')
      const current = await apiClient.get<AgentDocumentResponse>(
        `/agents/${id}/agent-document`,
        { params: { target: 'draft' } },
      )
      const document: AgentDocument = {
        ...current.document,
        analysis_schema: next,
      }
      return apiClient.put(`/agents/${id}/agent-document`, document)
    },
    onSuccess: () => {
      setSuccessMessage('Analysis schema saved.')
      setErrorMessage(null)
      queryClient.invalidateQueries({ queryKey: ['agent-document', id] })
      window.setTimeout(() => setSuccessMessage(null), 3000)
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to save analysis schema.'
      setErrorMessage(message)
      setSuccessMessage(null)
    },
  })

  const isDirty = useMemo(() => {
    const loaded = documentQuery.data?.document?.analysis_schema ?? []
    return JSON.stringify(loaded) !== JSON.stringify(schema)
  }, [documentQuery.data, schema])

  function updateVariable(index: number, patch: Partial<AnalysisVariableDef>) {
    setSchema((current) =>
      current.map((variable, i) =>
        i === index ? { ...variable, ...patch } : variable,
      ),
    )
  }

  function removeVariable(index: number) {
    setSchema((current) => current.filter((_, i) => i !== index))
  }

  function addVariable() {
    setSchema((current) => [...current, blankVariable()])
  }

  function handleSave() {
    const validationError = validateSchema(schema)
    if (validationError) {
      setErrorMessage(validationError)
      setSuccessMessage(null)
      return
    }
    setErrorMessage(null)
    saveMutation.mutate(schema)
  }

  return (
    <DashboardLayout>
      <div className="mx-auto max-w-4xl px-6 py-6 space-y-6">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate(`/agents/${id}/canvas`)}
              aria-label="Back to canvas"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <div>
              <h1 className="text-2xl font-semibold">Analysis Schema</h1>
              <p className="text-sm text-muted-foreground max-w-2xl">
                Reportable variables extracted at the end of every conversation. Each
                variable becomes a citation grounded to the exact utterance it came from.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={addVariable} variant="outline" size="sm">
              <Plus className="h-4 w-4 mr-1" /> Add variable
            </Button>
            <Button
              onClick={handleSave}
              size="sm"
              disabled={!isDirty || saveMutation.isPending}
            >
              <Save className="h-4 w-4 mr-1" />
              {saveMutation.isPending ? 'Saving…' : 'Save schema'}
            </Button>
          </div>
        </div>

        {errorMessage && (
          <Card>
            <CardContent className="pt-6">
              <p className="text-sm text-destructive">{errorMessage}</p>
            </CardContent>
          </Card>
        )}
        {successMessage && (
          <Card>
            <CardContent className="pt-6">
              <p className="text-sm text-green-600">{successMessage}</p>
            </CardContent>
          </Card>
        )}

        {documentQuery.isLoading ? (
          <Card>
            <CardContent className="pt-6">
              <p className="text-sm text-muted-foreground">Loading agent…</p>
            </CardContent>
          </Card>
        ) : schema.length === 0 ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Tag className="h-4 w-4" /> No variables yet
              </CardTitle>
              <CardDescription>
                Add variables you want extracted post-call — customer intent,
                resolution status, compliance flags. They appear on conversation
                detail as citations.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={addVariable} variant="outline">
                <Plus className="h-4 w-4 mr-1" /> Add your first variable
              </Button>
            </CardContent>
          </Card>
        ) : (
          schema.map((variable, index) => (
            <Card key={index}>
              <CardContent className="pt-6 space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <Label htmlFor={`name-${index}`}>Name</Label>
                    <Input
                      id={`name-${index}`}
                      value={variable.name}
                      onChange={(event) =>
                        updateVariable(index, { name: event.target.value })
                      }
                      placeholder="e.g. customer_intent"
                    />
                  </div>
                  <div>
                    <Label htmlFor={`type-${index}`}>Type</Label>
                    <Select
                      value={variable.type}
                      onValueChange={(value: AnalysisVariableType) =>
                        updateVariable(index, {
                          type: value,
                          categories: value === 'category' ? variable.categories ?? [] : null,
                        })
                      }
                    >
                      <SelectTrigger id={`type-${index}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TYPE_OPTIONS.map((option) => (
                          <SelectItem key={option} value={option}>
                            {option}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div>
                  <Label htmlFor={`description-${index}`}>Description</Label>
                  <Textarea
                    id={`description-${index}`}
                    value={variable.description}
                    onChange={(event) =>
                      updateVariable(index, { description: event.target.value })
                    }
                    placeholder="What this variable means — the LLM extractor uses this as a hint."
                    rows={2}
                  />
                </div>

                {variable.type === 'category' && (
                  <div>
                    <Label htmlFor={`categories-${index}`}>
                      Categories (comma-separated)
                    </Label>
                    <Input
                      id={`categories-${index}`}
                      value={categoriesToString(variable.categories)}
                      onChange={(event) =>
                        updateVariable(index, {
                          categories: parseCategories(event.target.value),
                        })
                      }
                      placeholder="e.g. resolved, escalated, pending"
                    />
                  </div>
                )}

                <div className="flex justify-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeVariable(index)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4 mr-1" /> Remove
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </DashboardLayout>
  )
}
