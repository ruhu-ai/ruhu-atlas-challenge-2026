import { useEffect } from 'react'
import { useBlocker } from 'react-router-dom'

/**
 * Prompt the user before React Router navigation when `when` is true.
 * Uses the React Router v6 `useBlocker` hook to intercept transitions and
 * surface a confirmation dialog.
 */
export function useNavigationPrompt(when: boolean, message: string) {
  const blocker = useBlocker(when)

  useEffect(() => {
    if (blocker.state === 'blocked') {
      const proceed = window.confirm(message)
      if (proceed) {
        blocker.proceed()
      } else {
        blocker.reset()
      }
    }
  }, [blocker, message])
}
