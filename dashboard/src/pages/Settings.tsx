import { useState } from 'react'

export default function Settings({ token: _token }: { token: string }) {
  const [testToken, setTestToken] = useState('')

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Settings</h1>

      <div className="space-y-6">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
          <h3 className="mb-2 font-semibold">Bot Status</h3>
          <p className="text-sm text-gray-400">
            The production bot runs as a separate worker process. It reads the same database
            as this dashboard, so changes you make here take effect immediately.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-green-500" />
            <span className="text-sm text-green-400">Connected (shares database)</span>
          </div>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
          <h3 className="mb-2 font-semibold">Test Bot Token</h3>
          <p className="mb-3 text-sm text-gray-400">
            Optionally set a second Telegram bot token for testing. Create a test bot via
            @BotFather, then paste its token below. Set it as the <code className="text-xs text-indigo-400">TEST_BOT_TOKEN</code> environment
            variable on your deployment.
          </p>
          <input
            value={testToken}
            onChange={(e) => setTestToken(e.target.value)}
            className="mb-3 w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            placeholder="123456:ABC-DEF..."
          />
          <p className="text-xs text-gray-500">
            Note: The test bot token must be set as an environment variable on the server.
            This field is for reference only.
          </p>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
          <h3 className="mb-2 font-semibold">Admin User IDs</h3>
          <p className="text-sm text-gray-400">
            Admin Telegram user IDs are configured in <code className="text-xs text-indigo-400">config.py</code> on the server.
            Admins can use /set, /mycmds, and /delete in the bot.
          </p>
          <div className="mt-3 rounded-lg bg-gray-800 px-4 py-3 font-mono text-sm text-gray-300">
            493310710, 6713100304, 8318575265
          </div>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
          <h3 className="mb-2 font-semibold">How to Test</h3>
          <ol className="ml-4 list-decimal space-y-2 text-sm text-gray-400">
            <li>Use the <strong>Flow Simulator</strong> on each club's page to preview deposit/cashout flows without Telegram.</li>
            <li>For live Telegram testing, create a test bot via @BotFather and set <code className="text-xs text-indigo-400">TEST_BOT_TOKEN</code>.</li>
            <li>Add the test bot to a private group and run through /deposit and /cashout flows.</li>
            <li>Both bots (prod + test) share the same database, so club configurations apply to both.</li>
          </ol>
        </div>
      </div>
    </div>
  )
}
