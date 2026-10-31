import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import { MeetingProvider } from './context/MeetingContext.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { MotionPreferenceProvider } from './context/MotionPreferenceContext.jsx'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <MeetingProvider>
        <MotionPreferenceProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </MotionPreferenceProvider>
      </MeetingProvider>
    </AuthProvider>
  </React.StrictMode>,
)