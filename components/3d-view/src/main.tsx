import { createRoot } from 'react-dom/client'
// Open Sans, the dashboard's typeface, bundled so the browser never calls a font service.
import '@fontsource/open-sans/400.css'
import '@fontsource/open-sans/600.css'
import '@fontsource/open-sans/700.css'
import './index.css'
import App from './App'

createRoot(document.getElementById('root')!).render(<App />)
