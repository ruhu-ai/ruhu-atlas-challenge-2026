import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Label } from '@/components/atoms/label'
import { Moon, Sun } from 'lucide-react'
import { useUIStore } from '@/store/ui.store'

export function AppearanceSettings() {
  const { theme, setTheme } = useUIStore()

  const options: { value: 'light' | 'dark'; label: string; icon: typeof Sun; description: string }[] = [
    {
      value: 'light',
      label: 'Light',
      icon: Sun,
      description: 'A clean, bright interface for well-lit environments',
    },
    {
      value: 'dark',
      label: 'Dark',
      icon: Moon,
      description: 'Premium obsidian theme, easier on the eyes in low light',
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Appearance</CardTitle>
        <CardDescription>
          Customize the look and feel of the application
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-3">
          <Label>Theme</Label>
          <div className="grid gap-3 sm:grid-cols-2">
            {options.map((option) => {
              const Icon = option.icon
              const isSelected = theme === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setTheme(option.value)}
                  className={`flex items-start gap-4 rounded-lg border-2 p-4 text-left transition-colors ${
                    isSelected
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:border-primary/50'
                  }`}
                >
                  <div
                    className={`mt-0.5 rounded-lg p-2 ${
                      isSelected
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    <Icon className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium">{option.label}</div>
                    <div className="mt-1 text-sm text-muted-foreground">
                      {option.description}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
