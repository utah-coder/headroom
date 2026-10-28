import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { loadScenarios } from './data/loadScenarios'

// Start both Scenario_File requests before the first render. The promise is
// cached, so App's effect reuses it and each file is fetched once (Requirement 17.1).
void loadScenarios()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
