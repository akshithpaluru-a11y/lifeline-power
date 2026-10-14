import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Shell } from './components/Shell'
import { boot } from './lib/store'
import Landing from './pages/Landing'
import Home from './pages/Home'
import Outage from './pages/Outage'
import Devices from './pages/Devices'
import People from './pages/People'
import Card from './pages/Card'
import Setup from './pages/Setup'
import Join from './pages/Join'
import Live from './pages/Live'

const Help = lazy(() => import('./pages/Help'))

export default function App() {
  useEffect(() => { boot() }, [])
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/setup" element={<Setup />} />
        <Route path="/join" element={<Join />} />
        <Route path="/live" element={<Live />} />
        <Route path="/app" element={<Shell />}>
          <Route index element={<Home />} />
          <Route path="outage" element={<Outage />} />
          <Route path="devices" element={<Devices />} />
          <Route path="people" element={<People />} />
          <Route path="help" element={<Suspense fallback={null}><Help /></Suspense>} />
          <Route path="card" element={<Card />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
