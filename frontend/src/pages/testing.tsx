/**
 * Evaluation Page
 *
 * Test voice agents with simulated conversations and track results.
 * Test voice agents with simulated conversations and track results.
 */

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Badge } from '@/components/atoms/badge'
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/atoms/dialog'
import { agentService } from '@/api/services/agent.service'
import { testingService, type TestCase, type TestRun } from '@/api/services/testing.service'
import { SimulationDashboard } from '@/features/testing/SimulationDashboard'
import {
  Play,
  Plus,
  CheckCircle2,
  XCircle,
  Clock,
  MessageSquare,
  Bot,
  User,
  Save,
  Trash2,
  Copy,
  Pencil,
  BarChart3,
  ListChecks,
} from 'lucide-react'
import { formatDate } from '@/lib/utils'

export default function TestingPage() {
  const [searchParams] = useSearchParams()
  const initialAgentFilter = searchParams.get('agent') || 'all'
  const [viewMode, setViewMode] = useState<'tests' | 'simulations'>('simulations')
  const [selectedAgent, setSelectedAgent] = useState<string>(initialAgentFilter)
  const [filterStatus, setFilterStatus] = useState<string>('all')
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [editingTestCase, setEditingTestCase] = useState<TestCase | null>(null)
  const [editTestName, setEditTestName] = useState('')
  const [editTestDescription, setEditTestDescription] = useState('')
  const [activeTestCase, setActiveTestCase] = useState<TestCase | null>(null)
  const [activeTestRun, setActiveTestRun] = useState<TestRun | null>(null)
  const [runningTestCaseId, setRunningTestCaseId] = useState<string | null>(null)
  const [newTestName, setNewTestName] = useState('')
  const [newTestAgent, setNewTestAgent] = useState('')
  const queryClient = useQueryClient()

  // Fetch agents for dropdown
  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentService.getAllAgents(),
  })

  useEffect(() => {
    const requestedAgentId = searchParams.get('agent')
    if (requestedAgentId && requestedAgentId !== selectedAgent) {
      setSelectedAgent(requestedAgentId)
      setViewMode('simulations')
    }
  }, [searchParams, selectedAgent])

  // Fetch test cases
  const { data: testCases = [], isLoading: isLoadingTestCases } = useQuery({
    queryKey: ['test-cases', selectedAgent],
    queryFn: () =>
      testingService.getTestCases({
        agent_id: selectedAgent === 'all' ? undefined : selectedAgent,
      }),
  })

  // Fetch test runs for active test case
  const { data: testRuns = [] } = useQuery({
    queryKey: ['test-runs', activeTestCase?.id],
    queryFn: () =>
      testingService.getTestRuns({
        test_case_id: activeTestCase?.id,
      }),
    enabled: !!activeTestCase,
  })

  // Create test case mutation
  const createTestMutation = useMutation({
    mutationFn: (data: { name: string; agent_id: string }) =>
      testingService.createTestCase({
        name: data.name,
        agent_id: data.agent_id,
        test_type: 'conversation',
        category: 'functional',
        priority: 'medium',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['test-cases'] })
      setIsCreateDialogOpen(false)
      setNewTestName('')
      setNewTestAgent('')
    },
  })

  // Delete test case mutation
  const deleteTestMutation = useMutation({
    mutationFn: (id: string) => testingService.deleteTestCase(id),
    onSuccess: (_, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['test-cases'] })
      if (activeTestCase && activeTestCase.id === deletedId) {
        setActiveTestCase(null)
      }
    },
  })

  // Duplicate test case mutation
  const duplicateTestMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      testingService.duplicateTestCase(id, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['test-cases'] })
    },
  })

  // Update test case mutation
  const updateTestMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { name?: string; description?: string } }) =>
      testingService.updateTestCase(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['test-cases'] })
      setIsEditDialogOpen(false)
      setEditingTestCase(null)
      setEditTestName('')
      setEditTestDescription('')
    },
  })

  // Run test case mutation
  const runTestMutation = useMutation({
    mutationFn: (testCaseId: string) => testingService.runTestCase(testCaseId),
    onSuccess: (testRun) => {
      queryClient.invalidateQueries({ queryKey: ['test-runs'] })
      setActiveTestRun(testRun)
    },
  })

  // Filter test cases
  const filteredTestCases = testCases.filter((tc) => {
    const matchesAgent = selectedAgent === 'all' || tc.agent_id === selectedAgent
    const matchesStatus =
      filterStatus === 'all' ||
      (filterStatus === 'passed' && tc.successful_runs > 0 && tc.failed_runs === 0) ||
      (filterStatus === 'failed' && tc.failed_runs > 0)
    return matchesAgent && matchesStatus
  })

  const handleRunTest = async (testCase: TestCase) => {
    setActiveTestCase(testCase)
    setRunningTestCaseId(testCase.id)
    try {
      await runTestMutation.mutateAsync(testCase.id)
    } finally {
      setRunningTestCaseId(null)
    }
  }

  const handleCreateTest = async () => {
    if (!newTestName || !newTestAgent) {
      toast.warning('Please provide a test name and select an agent')
      return
    }
    await createTestMutation.mutateAsync({
      name: newTestName,
      agent_id: newTestAgent,
    })
  }

  const handleDeleteTest = async (id: string) => {
    if (window.confirm('Are you sure you want to delete this test case?')) {
      await deleteTestMutation.mutateAsync(id)
    }
  }

  const handleDuplicateTest = async (testCase: TestCase) => {
    const newName = prompt('Enter name for duplicated test:', `${testCase.name} (Copy)`)
    if (newName) {
      await duplicateTestMutation.mutateAsync({ id: testCase.id, name: newName })
    }
  }

  const handleEditTest = (testCase: TestCase) => {
    setEditingTestCase(testCase)
    setEditTestName(testCase.name)
    setEditTestDescription(testCase.description || '')
    setIsEditDialogOpen(true)
  }

  const handleUpdateTest = async () => {
    if (!editingTestCase || !editTestName) {
      toast.warning('Please provide a test name')
      return
    }
    await updateTestMutation.mutateAsync({
      id: editingTestCase.id,
      data: {
        name: editTestName,
        description: editTestDescription,
      },
    })
  }

  const getStatusColor = (testCase: TestCase) => {
    if (testCase.failed_runs > 0) return 'destructive'
    if (testCase.successful_runs > 0) return 'success'
    return 'secondary'
  }

  const getStatusLabel = (testCase: TestCase) => {
    if (testCase.failed_runs > 0) return 'failed'
    if (testCase.successful_runs > 0) return 'passed'
    return 'pending'
  }

  const calculateSuccessRate = (testCase: TestCase) => {
    if (testCase.total_runs === 0) return 0
    return Math.round((testCase.successful_runs / testCase.total_runs) * 100)
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Page Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Evaluation</h1>
            <p className="mt-1 text-muted-foreground">
              Evaluate your agents with runtime-faithful conversations
            </p>
          </div>
          <div className="flex items-center gap-4">
            {/* View Mode Toggle */}
            <div className="flex items-center rounded-lg border border-border bg-card p-1">
              <Button
                variant={viewMode === 'simulations' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('simulations')}
                className={viewMode === 'simulations' ? 'bg-primary text-white' : ''}
              >
                <BarChart3 className="mr-2 h-4 w-4" />
                Evaluation
              </Button>
              <Button
                variant={viewMode === 'tests' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('tests')}
                className={viewMode === 'tests' ? 'bg-primary text-white' : ''}
              >
                <ListChecks className="mr-2 h-4 w-4" />
                Test Cases
              </Button>
            </div>
          <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="mr-2 h-4 w-4" />
                New Test Case
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Create Test Case</DialogTitle>
                <DialogDescription>
                  Define a test scenario for your voice agent
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="test-name">Test Name</Label>
                  <Input
                    id="test-name"
                    placeholder="e.g., Billing Inquiry - Happy Path"
                    value={newTestName}
                    onChange={(e) => setNewTestName(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="test-agent">Agent</Label>
                  <Select value={newTestAgent} onValueChange={setNewTestAgent}>
                    <SelectTrigger id="test-agent">
                      <SelectValue placeholder="Select agent" />
                    </SelectTrigger>
                    <SelectContent>
                      {agents?.map((agent) => (
                        <SelectItem key={agent.id} value={agent.id}>
                          {agent.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={() => setIsCreateDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={handleCreateTest}
                    disabled={createTestMutation.isPending}
                  >
                    {createTestMutation.isPending ? 'Creating...' : 'Create'}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>

          {/* Edit Test Case Dialog */}
          <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Edit Test Case</DialogTitle>
                <DialogDescription>
                  Update the test case name and description.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="edit-test-name">Test Name</Label>
                  <Input
                    id="edit-test-name"
                    placeholder="e.g., Billing Inquiry - Happy Path"
                    value={editTestName}
                    onChange={(e) => setEditTestName(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit-test-description">Description</Label>
                  <Input
                    id="edit-test-description"
                    placeholder="Optional description"
                    value={editTestDescription}
                    onChange={(e) => setEditTestDescription(e.target.value)}
                  />
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={() => setIsEditDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={handleUpdateTest}
                    disabled={updateTestMutation.isPending}
                  >
                    {updateTestMutation.isPending ? 'Updating...' : 'Update'}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
          </div>
        </div>

        {/* Evaluation Dashboard View */}
        {viewMode === 'simulations' && selectedAgent && selectedAgent !== 'all' && (
          <SimulationDashboard
            agentId={selectedAgent}
            agentName={agents?.find(a => a.id === selectedAgent)?.name}
          />
        )}

        {/* Evaluation - Agent Selection Required */}
        {viewMode === 'simulations' && (!selectedAgent || selectedAgent === 'all') && (
          <Card className="glass-card">
            <CardContent className="flex flex-col items-center justify-center py-16">
              <BarChart3 className="h-16 w-16 text-muted-foreground mb-4" />
              <h3 className="text-xl font-semibold mb-2">Select an Agent</h3>
              <p className="text-sm text-muted-foreground text-center max-w-md mb-6">
                Choose an agent to view its evaluation dashboard with pass rate, results, and run history.
              </p>
              <Select value={selectedAgent} onValueChange={setSelectedAgent}>
                <SelectTrigger className="w-64">
                  <SelectValue placeholder="Select an agent" />
                </SelectTrigger>
                <SelectContent>
                  {agents?.map((agent) => (
                    <SelectItem key={agent.id} value={agent.id}>
                      {agent.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </CardContent>
          </Card>
        )}

        {/* Test Cases View */}
        {viewMode === 'tests' && (
          <>
        {/* Filters */}
        <Card>
          <CardContent className="p-4">
            <div className="flex flex-wrap gap-4">
              <div className="w-64">
                <Select value={selectedAgent} onValueChange={setSelectedAgent}>
                  <SelectTrigger>
                    <SelectValue placeholder="Agent" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Agents</SelectItem>
                    {agents?.map((agent) => (
                      <SelectItem key={agent.id} value={agent.id}>
                        {agent.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex gap-2">
                <Button
                  variant={filterStatus === 'all' ? 'primary' : 'outline'}
                  size="sm"
                  onClick={() => setFilterStatus('all')}
                >
                  All
                </Button>
                <Button
                  variant={filterStatus === 'passed' ? 'primary' : 'outline'}
                  size="sm"
                  onClick={() => setFilterStatus('passed')}
                >
                  Passed
                </Button>
                <Button
                  variant={filterStatus === 'failed' ? 'primary' : 'outline'}
                  size="sm"
                  onClick={() => setFilterStatus('failed')}
                >
                  Failed
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* Test Cases List */}
          <Card>
            <CardHeader>
              <CardTitle>Test Cases ({filteredTestCases.length})</CardTitle>
            </CardHeader>
            <CardContent>
              {isLoadingTestCases ? (
                <div className="flex h-48 items-center justify-center">
                  <p className="text-muted-foreground">Loading test cases...</p>
                </div>
              ) : filteredTestCases.length === 0 ? (
                <div className="flex h-96 flex-col items-center justify-center text-muted-foreground">
                  <MessageSquare className="mb-2 h-12 w-12" />
                  <p>No test cases found</p>
                  <p className="text-sm">Create a test case to get started</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {filteredTestCases.map((testCase) => (
                    <div
                      key={testCase.id}
                      className={`rounded-lg border p-4 transition-colors hover:bg-accent/50 ${
                        activeTestCase?.id === testCase.id
                          ? 'border-primary bg-accent/20'
                          : 'border-border'
                      }`}
                      onClick={() => setActiveTestCase(testCase)}
                    >
                      {/* Header */}
                      <div className="mb-2 flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <h3 className="font-semibold">{testCase.name}</h3>
                            <Badge variant={getStatusColor(testCase)}>
                              {getStatusLabel(testCase)}
                            </Badge>
                          </div>
                          {testCase.description && (
                            <p className="mt-1 text-xs text-muted-foreground">
                              {testCase.description}
                            </p>
                          )}
                        </div>
                      </div>

                      {/* Metrics */}
                      <div className="mb-3 flex gap-4 text-xs text-muted-foreground">
                        <div className="flex items-center gap-1">
                          <CheckCircle2 className="h-3 w-3" />
                          <span>{calculateSuccessRate(testCase)}% success</span>
                        </div>
                        <div className="flex items-center gap-1">
                          <span>Runs: {testCase.total_runs}</span>
                        </div>
                        {testCase.last_run_at && (
                          <div>Last run: {formatDate(testCase.last_run_at)}</div>
                        )}
                      </div>

                      {/* Actions */}
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRunTest(testCase)
                          }}
                          disabled={runningTestCaseId !== null}
                        >
                          <Play className="mr-2 h-3 w-3" />
                          {runningTestCaseId === testCase.id ? 'Running...' : 'Run Test'}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleEditTest(testCase)
                          }}
                        >
                          <Pencil className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDuplicateTest(testCase)
                          }}
                          disabled={duplicateTestMutation.isPending}
                        >
                          <Copy className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDeleteTest(testCase.id)
                          }}
                          disabled={deleteTestMutation.isPending}
                        >
                          <Trash2 className="h-3 w-3 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Test Execution Panel */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Test Execution</CardTitle>
                {runTestMutation.isPending && (
                  <Badge variant="warning">
                    <Clock className="mr-1 h-3 w-3" />
                    Running...
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {!activeTestCase ? (
                <div className="flex h-96 flex-col items-center justify-center text-muted-foreground">
                  <MessageSquare className="mb-2 h-12 w-12" />
                  <p>Select a test case to view details</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {/* Test Info */}
                  <div className="rounded-lg border border-border bg-accent/20 p-3">
                    <div className="text-sm font-medium">{activeTestCase.name}</div>
                    {activeTestCase.description && (
                      <div className="mt-1 text-xs text-muted-foreground">
                        {activeTestCase.description}
                      </div>
                    )}
                  </div>

                  {/* Test Details */}
                  <div className="space-y-3">
                    <div className="text-sm font-medium">Test Details</div>
                    <div className="rounded-lg border border-border p-3">
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <div className="text-xs text-muted-foreground">Type</div>
                          <div className="text-sm font-medium">{activeTestCase.test_type}</div>
                        </div>
                        <div>
                          <div className="text-xs text-muted-foreground">Category</div>
                          <div className="text-sm font-medium">{activeTestCase.category}</div>
                        </div>
                        <div>
                          <div className="text-xs text-muted-foreground">Priority</div>
                          <div className="text-sm font-medium capitalize">
                            {activeTestCase.priority}
                          </div>
                        </div>
                        <div>
                          <div className="text-xs text-muted-foreground">Automated</div>
                          <div className="text-sm font-medium">
                            {activeTestCase.is_automated ? 'Yes' : 'No'}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Recent Test Runs */}
                  <div className="space-y-3">
                    <div className="text-sm font-medium">Recent Test Runs</div>
                    {testRuns.length === 0 ? (
                      <div className="rounded-lg border border-border p-4 text-center">
                        <p className="text-sm text-muted-foreground">
                          No test runs yet. Click "Run Test" to execute this test case.
                        </p>
                      </div>
                    ) : (
                      <div className="max-h-64 space-y-2 overflow-y-auto">
                        {testRuns.slice(0, 5).map((run) => (
                          <div
                            key={run.id}
                            className="rounded-lg border border-border p-3 hover:bg-accent/50"
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <Badge
                                  variant={
                                    run.status === 'completed'
                                      ? (run.pass_rate ?? 0) >= 1
                                        ? 'success'
                                        : 'destructive'
                                      : run.status === 'running'
                                      ? 'warning'
                                      : 'secondary'
                                  }
                                >
                                  {run.status}
                                </Badge>
                                <span className="text-sm">{run.run_name}</span>
                              </div>
                              <span className="text-xs text-muted-foreground">
                                {formatDate(run.started_at)}
                              </span>
                            </div>
                            {run.completed_at && run.duration_ms && (
                              <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
                                <span>Duration: {Math.round(run.duration_ms / 1000)}s</span>
                                <span>
                                  Passed: {run.passed_count} / {run.total_test_cases}
                                </span>
                                {run.pass_rate != null && (
                                  <span>Success: {((run.pass_rate ?? 0) * 100).toFixed(0)}%</span>
                                )}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Statistics */}
                  <div className="rounded-lg border border-border p-3">
                    <div className="mb-2 text-sm font-medium">Statistics</div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <div className="text-xs text-muted-foreground">Total Runs</div>
                        <div className="text-sm font-medium">{activeTestCase.total_runs}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">Successful</div>
                        <div className="text-sm font-medium text-green-500">
                          {activeTestCase.successful_runs}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">Failed</div>
                        <div className="text-sm font-medium text-red-500">
                          {activeTestCase.failed_runs}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">Success Rate</div>
                        <div className="text-sm font-medium">
                          {calculateSuccessRate(activeTestCase)}%
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
          </>
        )}
      </div>
    </DashboardLayout>
  )
}
