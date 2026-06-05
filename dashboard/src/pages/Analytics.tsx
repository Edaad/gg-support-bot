import PaymentMethodLinkingAnalytics from '../components/PaymentMethodLinkingAnalytics'

export default function Analytics({ token }: { token: string }) {
  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Analytics</h1>
      <p className="mb-6 text-sm text-slate-400">
        Venmo group-chat linking statistics across support groups.
      </p>
      <PaymentMethodLinkingAnalytics token={token} method="venmo" />
    </div>
  )
}
