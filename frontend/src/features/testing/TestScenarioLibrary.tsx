/**
 * Test Scenario Library Component
 *
 * Manage test scenarios for agent testing.
 * Create, edit, import/export, and organize test scenarios.
 */

import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Badge } from '@/components/atoms/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import {
  Plus,
  Search,
  Filter,
  Download,
  Upload,
  Play,
  Edit,
  Trash2,
  Copy,
  FileText,
} from 'lucide-react'
import { TestScenario } from './types'

interface TestScenarioLibraryProps {
  scenarios: TestScenario[]
  onCreateScenario: () => void
  onEditScenario: (scenario: TestScenario) => void
  onDeleteScenario: (scenarioId: string) => void
  onRunScenario: (scenarioId: string) => void
  onDuplicateScenario: (scenario: TestScenario) => void
  onImport: (file: File) => void
  onExport: (scenarioIds: string[]) => void
}

export function TestScenarioLibrary({
  scenarios,
  onCreateScenario,
  onEditScenario,
  onDeleteScenario,
  onRunScenario,
  onDuplicateScenario,
  onImport,
  onExport,
}: TestScenarioLibraryProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [selectedScenarios, setSelectedScenarios] = useState<Set<string>>(new Set())

  const filteredScenarios = scenarios.filter((scenario) => {
    const matchesSearch =
      scenario.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      scenario.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      scenario.tags.some((tag) => tag.toLowerCase().includes(searchQuery.toLowerCase()))

    const matchesCategory = categoryFilter === 'all' || scenario.category === categoryFilter

    return matchesSearch && matchesCategory
  })

  const handleSelectScenario = (scenarioId: string) => {
    const newSelected = new Set(selectedScenarios)
    if (newSelected.has(scenarioId)) {
      newSelected.delete(scenarioId)
    } else {
      newSelected.add(scenarioId)
    }
    setSelectedScenarios(newSelected)
  }

  const handleExportSelected = () => {
    onExport(Array.from(selectedScenarios))
  }

  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      onImport(file)
    }
  }

  const getCategoryColor = (category: TestScenario['category']) => {
    const colors = {
      functional: 'bg-blue-500/20 text-blue-600 dark:text-blue-400 border-blue-500/30',
      performance: 'bg-green-500/20 text-green-600 dark:text-green-400 border-green-500/30',
      conversation: 'bg-purple-500/20 text-purple-600 dark:text-purple-400 border-purple-500/30',
      'edge-case': 'bg-red-500/20 text-red-600 dark:text-red-400 border-red-500/30',
    }
    return colors[category] || 'bg-muted text-muted-foreground border-border'
  }

  return (
    <div className="space-y-4">
      {/* Header Actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 flex-1 max-w-md">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search scenarios..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>
          <Select value={categoryFilter} onValueChange={setCategoryFilter}>
            <SelectTrigger className="w-40">
              <Filter className="mr-2 h-4 w-4" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Categories</SelectItem>
              <SelectItem value="functional">Functional</SelectItem>
              <SelectItem value="performance">Performance</SelectItem>
              <SelectItem value="conversation">Conversation</SelectItem>
              <SelectItem value="edge-case">Edge Cases</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          {selectedScenarios.size > 0 && (
            <Button variant="outline" onClick={handleExportSelected}>
              <Download className="mr-2 h-4 w-4" />
              Export ({selectedScenarios.size})
            </Button>
          )}
          <label className="cursor-pointer">
            <span className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2">
              <Upload className="mr-2 h-4 w-4" />
              Import
            </span>
            <input
              type="file"
              accept=".json"
              onChange={handleImport}
              className="hidden"
            />
          </label>
          <Button onClick={onCreateScenario} className="bg-primary hover:bg-primary/90">
            <Plus className="mr-2 h-4 w-4" />
            New Scenario
          </Button>
        </div>
      </div>

      {/* Scenarios Grid */}
      {filteredScenarios.length === 0 ? (
        <Card className="glass-card">
          <CardContent className="flex flex-col items-center justify-center py-12">
            <FileText className="h-12 w-12 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">No scenarios found</h3>
            <p className="text-sm text-muted-foreground mb-4">
              {searchQuery || categoryFilter !== 'all'
                ? 'Try adjusting your filters'
                : 'Create your first test scenario to get started'}
            </p>
            {!searchQuery && categoryFilter === 'all' && (
              <Button onClick={onCreateScenario}>
                <Plus className="mr-2 h-4 w-4" />
                Create Scenario
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filteredScenarios.map((scenario) => (
            <Card
              key={scenario.id}
              className={`glass-card cursor-pointer transition-all hover:border-primary/50 ${
                selectedScenarios.has(scenario.id) ? 'border-primary bg-primary/5' : ''
              }`}
              onClick={() => handleSelectScenario(scenario.id)}
            >
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <CardTitle className="text-base mb-2">{scenario.name}</CardTitle>
                    <CardDescription className="line-clamp-2">
                      {scenario.description}
                    </CardDescription>
                  </div>
                  <input
                    type="checkbox"
                    checked={selectedScenarios.has(scenario.id)}
                    onChange={() => handleSelectScenario(scenario.id)}
                    onClick={(e) => e.stopPropagation()}
                    className="rounded"
                  />
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge className={getCategoryColor(scenario.category)}>
                    {scenario.category}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {scenario.steps.length} steps
                  </span>
                </div>

                {scenario.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {scenario.tags.slice(0, 3).map((tag) => (
                      <Badge key={tag} variant="outline" className="text-xs">
                        {tag}
                      </Badge>
                    ))}
                    {scenario.tags.length > 3 && (
                      <Badge variant="outline" className="text-xs">
                        +{scenario.tags.length - 3}
                      </Badge>
                    )}
                  </div>
                )}

                <div className="flex items-center justify-between pt-2 border-t border-border">
                  <span className="text-xs text-muted-foreground">
                    {new Date(scenario.updatedAt).toLocaleDateString()}
                  </span>
                  <div className="flex items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={(e) => {
                        e.stopPropagation()
                        onRunScenario(scenario.id)
                      }}
                      className="h-8 w-8 p-0"
                    >
                      <Play className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={(e) => {
                        e.stopPropagation()
                        onEditScenario(scenario)
                      }}
                      className="h-8 w-8 p-0"
                    >
                      <Edit className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={(e) => {
                        e.stopPropagation()
                        onDuplicateScenario(scenario)
                      }}
                      className="h-8 w-8 p-0"
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={(e) => {
                        e.stopPropagation()
                        if (confirm('Delete this scenario?')) {
                          onDeleteScenario(scenario.id)
                        }
                      }}
                      className="h-8 w-8 p-0 text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Summary */}
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          Showing {filteredScenarios.length} of {scenarios.length} scenarios
        </span>
        {selectedScenarios.size > 0 && (
          <span>{selectedScenarios.size} selected</span>
        )}
      </div>
    </div>
  )
}
