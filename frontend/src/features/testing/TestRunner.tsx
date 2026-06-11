/**
 * Test Runner Component
 *
 * Execute test scenarios and display real-time progress.
 * Shows live status of each test step as it runs.
 */

import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Progress } from '@/components/atoms/progress'
import { Badge } from '@/components/atoms/badge'
import {
  Play,
  Pause,
  Square,
  CheckCircle,
  XCircle,
  Clock,
  AlertCircle,
  Loader2,
} from 'lucide-react'
import { TestRun, TestScenario, TestResult } from './types'

interface TestRunnerProps {
  scenario: TestScenario
  isRunning: boolean
  testRun?: TestRun
  onStart: () => void
  onPause: () => void
  onStop: () => void
  onComplete: (run: TestRun) => void
}

export function TestRunner({
  scenario,
  isRunning,
  testRun,
  onStart,
  onPause,
  onStop,
  onComplete,
}: TestRunnerProps) {
  const [currentStep, setCurrentStep] = useState(0)
  const [elapsedTime, setElapsedTime] = useState(0)

  // Simulate test execution (will be replaced with actual API calls)
  useEffect(() => {
    if (isRunning && testRun) {
      const timer = setInterval(() => {
        setElapsedTime((prev) => prev + 1)
      }, 1000)

      return () => clearInterval(timer)
    }
  }, [isRunning, testRun])

  const progress = testRun?.metadata
    ? ((testRun.metadata.passedSteps || 0) + (testRun.metadata.failedSteps || 0) + (testRun.metadata.skippedSteps || 0)) /
      (testRun.metadata.totalSteps || 1) *
      100
    : 0

  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const getStepStatus = (stepId: string): TestResult | undefined => {
    return testRun?.results?.find((r) => r.stepId === stepId)
  }

  const getStepIcon = (stepId: string) => {
    const result = getStepStatus(stepId)
    if (!result) {
      return <Clock className="h-4 w-4 text-muted-foreground" />
    }

    switch (result.status) {
      case 'passed':
        return <CheckCircle className="h-4 w-4 text-green-500" />
      case 'failed':
        return <XCircle className="h-4 w-4 text-red-500" />
      case 'skipped':
        return <AlertCircle className="h-4 w-4 text-yellow-500" />
      default:
        return <Clock className="h-4 w-4 text-muted-foreground" />
    }
  }

  return (
    <div className="space-y-4">
      {/* Test Info Header */}
      <Card className="glass-card">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-xl">{scenario.name}</CardTitle>
              <p className="text-sm text-muted-foreground mt-1">{scenario.description}</p>
            </div>
            <div className="flex items-center gap-2">
              {!isRunning && !testRun && (
                <Button onClick={onStart} className="bg-green-600 hover:bg-green-700">
                  <Play className="mr-2 h-4 w-4" />
                  Start Test
                </Button>
              )}
              {isRunning && (
                <>
                  <Button onClick={onPause} variant="outline">
                    <Pause className="mr-2 h-4 w-4" />
                    Pause
                  </Button>
                  <Button onClick={onStop} variant="outline" className="text-red-600 dark:text-red-400">
                    <Square className="mr-2 h-4 w-4" />
                    Stop
                  </Button>
                </>
              )}
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* Progress Overview */}
      {testRun && (
        <Card className="glass-card">
          <CardContent className="pt-6">
            <div className="space-y-4">
              {/* Progress Bar */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium">
                    Progress: {Math.round(progress)}%
                  </span>
                  <span className="text-sm text-muted-foreground">
                    {formatTime(elapsedTime)}
                  </span>
                </div>
                <Progress value={progress} className="h-2" />
              </div>

              {/* Stats Grid */}
              <div className="grid grid-cols-4 gap-4">
                <div className="text-center p-3 rounded-lg bg-muted">
                  <div className="text-2xl font-bold text-foreground">
                    {testRun.metadata.totalSteps}
                  </div>
                  <div className="text-xs text-muted-foreground">Total Steps</div>
                </div>
                <div className="text-center p-3 rounded-lg bg-green-500/10 border border-green-500/20">
                  <div className="text-2xl font-bold text-green-600 dark:text-green-400">
                    {testRun.metadata.passedSteps}
                  </div>
                  <div className="text-xs text-green-600 dark:text-green-300">Passed</div>
                </div>
                <div className="text-center p-3 rounded-lg bg-red-500/10 border border-red-500/20">
                  <div className="text-2xl font-bold text-red-600 dark:text-red-400">
                    {testRun.metadata.failedSteps}
                  </div>
                  <div className="text-xs text-red-600 dark:text-red-300">Failed</div>
                </div>
                <div className="text-center p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
                  <div className="text-2xl font-bold text-yellow-600 dark:text-yellow-400">
                    {testRun.metadata.skippedSteps}
                  </div>
                  <div className="text-xs text-yellow-600 dark:text-yellow-300">Skipped</div>
                </div>
              </div>

              {/* Status Badge */}
              <div className="flex items-center justify-center gap-2">
                {testRun.status === 'running' && (
                  <Badge className="bg-blue-500/20 text-blue-600 dark:text-blue-400 border-blue-500/30">
                    <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                    Running
                  </Badge>
                )}
                {testRun.status === 'completed' && (
                  <Badge className="bg-green-500/20 text-green-600 dark:text-green-400 border-green-500/30">
                    <CheckCircle className="mr-2 h-3 w-3" />
                    Completed
                  </Badge>
                )}
                {testRun.status === 'failed' && (
                  <Badge className="bg-red-500/20 text-red-600 dark:text-red-400 border-red-500/30">
                    <XCircle className="mr-2 h-3 w-3" />
                    Failed
                  </Badge>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Test Steps */}
      <Card className="glass-card">
        <CardHeader>
          <CardTitle className="text-base">Test Steps</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {(scenario.steps || []).map((step, index) => {
              const result = getStepStatus(step.id)
              const isCurrent = isRunning && index === currentStep
              const isCompleted = result !== undefined

              return (
                <div
                  key={step.id}
                  className={`p-3 rounded-lg border transition-all ${
                    isCurrent
                      ? 'border-primary bg-primary/10'
                      : isCompleted
                      ? result.status === 'passed'
                        ? 'border-green-500/30 bg-green-500/5'
                        : result.status === 'failed'
                        ? 'border-red-500/30 bg-red-500/5'
                        : 'border-yellow-500/30 bg-yellow-500/5'
                      : 'border-border bg-card/50'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className="flex items-center justify-center w-6 h-6 rounded-full bg-muted flex-shrink-0">
                      {isCurrent ? (
                        <Loader2 className="h-4 w-4 animate-spin text-primary" />
                      ) : (
                        getStepIcon(step.id)
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-medium">
                          Step {step.order}: {step.type}
                        </span>
                        {result && (
                          <span className="text-xs text-muted-foreground">
                            {result.duration}ms
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground break-words">
                        {step.content}
                      </p>
                      {result && result.message && (
                        <p className={`text-xs mt-2 ${
                          result.status === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'
                        }`}>
                          {result.message}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </CardContent>
      </Card>

      {/* Expected Outcomes */}
      <Card className="glass-card">
        <CardHeader>
          <CardTitle className="text-base">Expected Outcomes</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {(scenario.expectedOutcomes || []).map((outcome) => (
              <div
                key={outcome.id}
                className="flex items-start gap-3 p-3 rounded-lg border border-border bg-card/50"
              >
                <CheckCircle className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-0.5" />
                <div className="flex-1">
                  <p className="text-sm font-medium">{outcome.description}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <Badge variant="outline" className="text-xs">
                      {outcome.type}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {outcome.target}: {String(outcome.value)}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
