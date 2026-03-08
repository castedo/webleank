import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import ControlPanel from './ControlPanel.tsx'

const root = createRoot(document.getElementById('root')!)
root.render(
  <StrictMode>
    <ControlPanel />
  </StrictMode>,
)
