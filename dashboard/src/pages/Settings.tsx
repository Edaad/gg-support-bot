import { useId, useState } from 'react'

export default function Settings({ token: _token }: { token: string }) {
  const [testToken, setTestToken] = useState('')
  const testTokenId = useId()

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-balance">Settings</h1>

      <div className="space-y-6">
        <section className="panel">
          <h2 className="mb-2 text-lg font-semibold text-ink">Bot status</h2>
          <p className="text-sm text-ink-muted">
            The production bot runs as a separate worker process. It reads the same database as this
            dashboard, so changes you make here take effect immediately.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-success-ink" aria-hidden />
            <span className="text-sm text-success-ink">Connected (shares database)</span>
          </div>
        </section>

        <section className="panel">
          <h2 className="mb-2 text-lg font-semibold text-ink">Test bot token</h2>
          <p className="mb-3 text-sm text-ink-muted">
            Optionally set a second Telegram bot token for testing. Create a test bot via @BotFather,
            then paste its token below. Set it as the <code>TEST_BOT_TOKEN</code> environment variable on
            your deployment.
          </p>
          <label htmlFor={testTokenId} className="label-field">
            Token (reference only)
          </label>
          <input
            id={testTokenId}
            value={testToken}
            onChange={(e) => setTestToken(e.target.value)}
            className="input-field mb-3"
            placeholder="123456:ABC-DEF..."
            autoComplete="off"
          />
          <p className="text-xs text-ink-muted">
            The test bot token must be set as an environment variable on the server. This field is
            for reference only.
          </p>
        </section>

        <section className="panel">
          <h2 className="mb-2 text-lg font-semibold text-ink">Admin user IDs</h2>
          <p className="text-sm text-ink-muted">
            Admin Telegram user IDs are configured in <code>config.py</code> on the server. Admins can
            use /set, /mycmds, and /delete in the bot.
          </p>
          <div className="mt-3 rounded-lg bg-surface-raised px-4 py-3 font-mono text-sm text-ink">
            493310710, 6713100304, 8318575265
          </div>
        </section>

        <section className="panel">
          <h2 className="mb-2 text-lg font-semibold text-ink">How to test</h2>
          <ol className="ml-4 list-decimal space-y-2 text-sm text-ink-muted">
            <li>
              Use the <strong className="text-ink">flow simulator</strong> on each club&apos;s page to
              preview deposit and cashout flows without Telegram.
            </li>
            <li>
              For live Telegram testing, create a test bot via @BotFather and set{' '}
              <code>TEST_BOT_TOKEN</code>.
            </li>
            <li>Add the test bot to a private group and run through /deposit and /cashout flows.</li>
            <li>Both bots (production and test) share the same database, so club configs apply to both.</li>
          </ol>
        </section>
      </div>
    </div>
  )
}
