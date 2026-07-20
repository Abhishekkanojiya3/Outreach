import { useState, useEffect } from 'react'
import api from '../api'

export default function Settings() {
  const [form, setForm] = useState({
    gmail_address: '',
    gmail_app_password: '',
    openai_api_key: '',
    send_delay_seconds: 60,
    tracking_base_url: '',
  })
  const [hasPassword, setHasPassword] = useState(false)
  const [hasKey, setHasKey] = useState(false)
  const [flash, setFlash] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/settings')
      .then((res) => {
        setForm((prev) => ({
          ...prev,
          gmail_address: res.data.gmail_address,
          send_delay_seconds: res.data.send_delay_seconds,
          tracking_base_url: res.data.tracking_base_url || '',
        }))
        setHasPassword(res.data.has_gmail_password)
        setHasKey(res.data.has_openai_key)
      })
      .catch(console.error)
  }, [])

  const handleChange = (e) => {
    const { name, value } = e.target
    setForm((prev) => ({
      ...prev,
      [name]: name === 'send_delay_seconds' ? parseInt(value, 10) : value,
    }))
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setFlash('')
    setSaving(true)

    try {
      await api.post('/settings', form)
      setFlash('Settings saved successfully!')
      if (form.gmail_app_password) setHasPassword(true)
      if (form.openai_api_key) setHasKey(true)
      setTimeout(() => setFlash(''), 3000)
    } catch (err) {
      setError(err.response?.data?.error || 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div id="settings-page" className="max-w-2xl mx-auto pb-12">
      <div className="mb-8 pb-4 border-b border-zinc-200">
        <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">Settings</h1>
      </div>

      {flash ? (
        <div className="flash-message mb-6 p-4 bg-emerald-50 border border-emerald-200 text-emerald-700 font-mono text-sm rounded-none">
          {flash}
        </div>
      ) : null}

      {error ? (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 text-red-700 font-mono text-sm rounded-none">
          {error}
        </div>
      ) : null}

      <form onSubmit={handleSubmit} className="space-y-6 bg-white p-8 border border-zinc-200">
        {/* Gmail Address */}
        <div>
          <label htmlFor="gmail-address" className="block text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-2">
            Gmail Address
          </label>
          <input
            id="gmail-address"
            type="email"
            name="gmail_address"
            value={form.gmail_address}
            onChange={handleChange}
            placeholder="youremail@gmail.com"
            className="w-full border border-zinc-300 bg-zinc-50 rounded-none px-4 py-3 font-mono text-sm focus:bg-white focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none transition-colors"
          />
        </div>

        {/* Gmail App Password */}
        <div>
          <label htmlFor="gmail-password" className="block text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-2 flex items-center justify-between">
            Gmail App Password
            {hasPassword ? (
              <span className="text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 inline-block">✓ CONFIGURED</span>
            ) : null}
          </label>
          <input
            id="gmail-password"
            type="password"
            name="gmail_app_password"
            value={form.gmail_app_password}
            onChange={handleChange}
            placeholder={hasPassword ? '••••••••••••••••' : 'Enter your app password'}
            className="w-full border border-zinc-300 bg-zinc-50 rounded-none px-4 py-3 font-mono text-sm focus:bg-white focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none transition-colors"
          />
          <p className="mt-2 text-[11px] font-mono text-zinc-500 leading-relaxed">
            To generate a Gmail App Password, go to{' '}
            <a
              href="https://myaccount.google.com/security"
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 hover:text-indigo-800 underline decoration-indigo-300 underline-offset-4"
            >
              Google Account → Security
            </a>{' '}
            → 2-Step Verification → App Passwords.
          </p>
        </div>

        {/* OpenAI API Key */}
        <div>
          <label htmlFor="openai-key" className="block text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-2 flex items-center justify-between">
            OpenAI API Key
            {hasKey ? (
              <span className="text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 inline-block">✓ CONFIGURED</span>
            ) : null}
          </label>
          <input
            id="openai-key"
            type="password"
            name="openai_api_key"
            value={form.openai_api_key}
            onChange={handleChange}
            placeholder={hasKey ? '••••••••••••••••' : 'Enter your OpenAI API key'}
            className="w-full border border-zinc-300 bg-zinc-50 rounded-none px-4 py-3 font-mono text-sm focus:bg-white focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none transition-colors"
          />
          <p className="mt-2 text-[11px] font-mono text-zinc-500 leading-relaxed">
            Used to generate emails, parse your resume, resolve company names, and classify
            inbox replies. Get a key at{' '}
            <a
              href="https://platform.openai.com/api-keys"
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 hover:text-indigo-800 underline decoration-indigo-300 underline-offset-4"
            >
              platform.openai.com/api-keys
            </a>.
          </p>
        </div>

        {/* Open Tracking Base URL */}
        <div>
          <label htmlFor="tracking-url" className="block text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-2">
            Open Tracking URL (optional)
          </label>
          <input
            id="tracking-url"
            type="url"
            name="tracking_base_url"
            value={form.tracking_base_url}
            onChange={handleChange}
            placeholder="https://your-tunnel.ngrok-free.app"
            className="w-full border border-zinc-300 bg-zinc-50 rounded-none px-4 py-3 font-mono text-sm focus:bg-white focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none transition-colors"
          />
          <p className="mt-2 text-[11px] font-mono text-zinc-500 leading-relaxed">
            Public URL of this backend, used for the invisible open-tracking pixel.
            It must be reachable from the internet (recipients&apos; mail apps fetch the pixel) —
            e.g. run <span className="text-zinc-800">ngrok http 5000</span> and paste the https URL here.
            Leave empty to disable open tracking (emails are then sent as plain text).
          </p>
        </div>

        {/* Send Delay */}
        <div className="pb-6 border-b border-zinc-200">
          <label htmlFor="send-delay" className="block text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-4">
            Delay Between Emails: <span className="text-indigo-700 font-mono text-sm">{form.send_delay_seconds}s</span>
          </label>
          <input
            id="send-delay"
            type="range"
            name="send_delay_seconds"
            min="20"
            max="60"
            value={form.send_delay_seconds}
            onChange={handleChange}
            className="w-full accent-indigo-700 h-2 bg-zinc-200 rounded-none appearance-none cursor-pointer"
          />
          <div className="flex justify-between text-[10px] font-mono text-zinc-400 uppercase tracking-widest mt-2">
            <span>20s</span>
            <span>60s</span>
          </div>
        </div>

        <div className="pt-2">
          <button
            id="save-settings-btn"
            type="submit"
            disabled={saving}
            className="w-full py-4 bg-indigo-700 text-[#f6f8fc] font-display font-bold rounded-none hover:bg-indigo-800 disabled:opacity-50 transition-colors uppercase tracking-widest text-xs"
          >
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </form>
    </div>
  )
}
