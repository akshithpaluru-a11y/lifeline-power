import { AnimatePresence, motion } from 'motion/react'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
          <motion.div
            role="dialog" aria-label={title}
            className="relative w-full max-w-[440px] rounded-t-[28px] sm:rounded-[28px] bg-ink-900 ring-1 ring-white/10 p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] max-h-[92dvh] overflow-y-auto"
            initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }}
            transition={{ type: 'spring', damping: 30, stiffness: 320 }}
          >
            <div className="mx-auto mb-4 h-1 w-9 rounded-full bg-white/15 sm:hidden" />
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-[17px] font-semibold tracking-tight">{title}</h2>
              <button onClick={onClose} className="grid size-8 place-items-center rounded-full bg-white/5 text-fg-2 hover:bg-white/10" aria-label="Close"><X size={16} /></button>
            </div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
