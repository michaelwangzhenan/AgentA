import { Navigate, useNavigate, useParams } from 'react-router-dom'

import { SettingsPage } from '@/components/settings/SettingsPage'
import { isSettingsSection, type SettingsSection } from '@/routes/paths'

export function SettingsRoutePage() {
  const { section: sectionParam } = useParams()
  const navigate = useNavigate()
  const section: SettingsSection =
    sectionParam && isSettingsSection(sectionParam) ? sectionParam : 'profile'

  return (
    <SettingsPage
      section={section}
      onSectionChange={(next) => navigate(`/settings/${next}`)}
    />
  )
}

export function SettingsIndexRedirect() {
  return <Navigate to="/settings/profile" replace />
}

export function SettingsSectionGuard() {
  const { section } = useParams()
  if (section && isSettingsSection(section)) {
    return <SettingsRoutePage />
  }
  return <Navigate to="/settings/profile" replace />
}
