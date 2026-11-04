import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.jsx'
import { registrarVaciadoDeSesion } from './config/sessionTeardown'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2 * 60 * 1000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

// A-28: el cierre INVOLUNTARIO de sesión (config/api.js, cuando el propio
// refresh devuelve 401/403) borraba los tokens y dejaba la caché dentro, con
// los matches, el perfil, los guardados y los borradores de la cuenta que
// acababa de salir. `useLogout` sí la vaciaba y su comentario promete que «nada
// de la sesión que acaba sobrevive a su cierre»: esto hace que valga para los
// DOS caminos. Se registra aquí porque main.jsx es el único sitio que ya conoce
// react-query — api.js no debe depender de él.
registrarVaciadoDeSesion(() => queryClient.clear())

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
