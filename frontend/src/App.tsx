export default function App() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
      <section className="mx-auto max-w-4xl rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-2xl">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-400">DriftWatch</p>
        <h1 className="mt-4 text-3xl font-bold">Infrastructure visibility starts here.</h1>
        <p className="mt-3 max-w-2xl text-slate-400">
          The local development foundation is running. Connect a project and inspect real drift
          findings as the scanning and dashboard phases are implemented.
        </p>
        <div className="mt-8 flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-950 p-4">
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" aria-hidden="true" />
          <span className="text-sm text-slate-300">API foundation available</span>
          <a className="ml-auto text-sm text-cyan-400 hover:text-cyan-300" href="/health/ready">
            Check readiness
          </a>
        </div>
      </section>
    </main>
  )
}
