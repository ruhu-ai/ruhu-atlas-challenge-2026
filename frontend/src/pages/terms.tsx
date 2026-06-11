import { Link } from 'react-router-dom'

export default function TermsPage() {
  return (
    <div className="mx-auto max-w-3xl p-6 md:p-10">
      <h1 className="text-3xl font-bold">Terms of Service</h1>
      <p className="mt-4 text-muted-foreground">
        These terms govern use of the Ruhu AI platform. By using the service, you agree to comply
        with applicable laws, acceptable-use restrictions, and account security requirements.
      </p>
      <p className="mt-4 text-muted-foreground">
        For enterprise contracts and data processing agreements, contact your account representative.
      </p>
      <Link to="/register" className="mt-8 inline-block text-primary hover:underline">
        Back to registration
      </Link>
    </div>
  )
}
