import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

type HealthResponse = {
  ok: boolean
  version: string
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const checkHealth = () => {
    setError(null)
    setHealth(null)
    fetch('/api/health')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<HealthResponse>
      })
      .then(setHealth)
      .catch((e: Error) => setError(e.message))
  }

  useEffect(() => {
    checkHealth()
  }, [])

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <div className="bg-card text-card-foreground border border-border rounded-xl shadow-sm p-8 max-w-md w-full space-y-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AgentA Web UI</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Step 0 - 项目骨架验证
          </p>
        </div>
        <div className="text-sm">
          <span className="text-muted-foreground">API health: </span>
          {error ? (
            <span className="text-destructive font-mono">ERROR — {error}</span>
          ) : health ? (
            <span className="text-green-600 dark:text-green-400 font-mono">
              OK ✓ (version {health.version})
            </span>
          ) : (
            <span className="text-muted-foreground">checking…</span>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={checkHealth}>
          重新检测
        </Button>
      </div>
    </div>
  )
}

export default App
