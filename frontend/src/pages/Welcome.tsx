import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { ArrowRight, Sparkles } from 'lucide-react'
import { Logo, LogoMark } from '../components/Logo'
import { Button } from '../components/Button'
import { createDemoHome } from '../lib/store'

// Shown at /app before a home exists.
export function Welcome() {
  const [busy, setBusy] = useState(false)
  return (
    <div className="min-h-dvh bg-ink-950">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-[480px] bg-[radial-gradient(60%_100%_at_50%_0%,rgb(255_178_36/.10),transparent)]" />
      <div className="relative mx-auto flex min-h-dvh max-w-[440px] flex-col px-5 pb-8 pt-[max(1rem,env(safe-area-inset-top))]">
        <Link to="/"><Logo size={26} /></Link>
        <div className="relative my-auto grid place-items-center py-10">
          {[0, 1, 2].map((i) => (
            <span key={i} className="absolute size-40 rounded-full ring-1 ring-amber/30" style={{ animation: `ping-soft 3.6s var(--ease-out-expo) ${i * 1.2}s infinite` }} />
          ))}
          <div className="absolute size-48 rounded-full bg-amber/15 blur-3xl" />
          <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', damping: 14 }}>
            <LogoMark size={112} className="relative drop-shadow-[0_20px_40px_rgb(255_178_36/.35)]" />
          </motion.div>
        </div>
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}>
          <h1 className="text-[40px] font-semibold leading-[1] tracking-[-0.04em]">Let’s set up<br />your home.</h1>
          <p className="mt-4 text-[15px] leading-relaxed text-fg-3">Two minutes: the devices that need power, any backup batteries, and who to alert.</p>
        </motion.div>
        <div className="mt-10 space-y-2.5">
          <Link to="/setup" className="flex h-14 w-full items-center justify-center gap-2 rounded-[18px] bg-gradient-to-b from-amber-hi to-amber text-[16px] font-medium text-ink-950 shadow-[inset_0_1px_0_rgb(255_255_255/.5),0_8px_24px_-8px_rgb(255_178_36/.6)]">
            Set up my home <ArrowRight size={18} />
          </Link>
          <Button variant="secondary" size="lg" className="w-full" disabled={busy} icon={<Sparkles size={17} />}
            onClick={async () => { setBusy(true); try { await createDemoHome() } finally { setBusy(false) } }}>
            {busy ? 'Building sample home…' : 'Explore a sample home'}
          </Button>
          <Link to="/join" className="block pt-2 text-center text-[13px] text-fg-3 hover:text-fg">Have a family code? Join as a caregiver</Link>
        </div>
      </div>
    </div>
  )
}
