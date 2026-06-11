import { Link } from 'react-router-dom'

export default function PrivacyPage() {
  return (
    <div className="mx-auto max-w-3xl p-6 md:p-10">
      <h1 className="text-3xl font-bold">Privacy Policy</h1>
      <p className="mt-4 text-muted-foreground">
        Ruhu AI processes customer data to provide voice-agent features, analytics, and support.
        Data is handled according to contractual obligations and applicable privacy regulations.
      </p>
      <p className="mt-4 text-muted-foreground">
        If you need a copy of your data or want to request deletion, contact your administrator or
        support team.
      </p>
      <Link to="/register" className="mt-8 inline-block text-primary hover:underline">
        Back to registration
      </Link>
    </div>
  )
}
