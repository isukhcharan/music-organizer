import { useEffect, useState } from 'react'
import './App.css'
import { Home } from './components/Home'
import { JobView } from './components/JobView'
import { RepairView } from './components/RepairView'

type Route = { page: 'home' } | { page: 'repair' } | { page: 'job'; id: string }

const parse = (): Route => {
  if (location.hash === '#/repair') return { page: 'repair' }
  const m = location.hash.match(/^#\/job\/(.+)$/)
  return m ? { page: 'job', id: decodeURIComponent(m[1]) } : { page: 'home' }
}

export default function App() {
  const [route, setRoute] = useState<Route>(parse())

  useEffect(() => {
    const onHash = () => setRoute(parse())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const go = (hash: string) => {
    location.hash = hash
    setRoute(parse())
  }

  return (
    <div className="app">
      <header>
        <button className="link brand" onClick={() => go('')}>♫ Music Organizer</button>
        <nav className="topnav">
          <button className={route.page !== 'repair' ? 'active' : ''} onClick={() => go('')}>Organise</button>
          <button className={route.page === 'repair' ? 'active' : ''} onClick={() => go('#/repair')}>Repair tags</button>
        </nav>
        <span className="muted">local only · nothing leaves this computer except provider searches</span>
      </header>
      <main>
        {route.page === 'repair' && <RepairView />}
        {route.page === 'job' && <JobView key={route.id} id={route.id} />}
        {route.page === 'home' && <Home open={(id) => go(`#/job/${encodeURIComponent(id)}`)} />}
      </main>
    </div>
  )
}
