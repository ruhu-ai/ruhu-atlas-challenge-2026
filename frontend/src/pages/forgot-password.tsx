import { Navigate } from 'react-router-dom'

// Password-based reset is not used — authentication is via magic link / OAuth.
export default function ForgotPasswordPage() {
  return <Navigate to="/login" replace />
}
