import { Navigate, Route, Routes } from 'react-router-dom'

import { BackupView } from '@/components/admin/BackupView'
import { MemoryView } from '@/components/resources/MemoryView'
import { MCPView } from '@/components/resources/MCPView'
import { RulesView } from '@/components/resources/RulesView'
import { SkillsView } from '@/components/resources/SkillsView'
import { AppLayout } from '@/routes/AppLayout'
import { RouteGuard } from '@/routes/RouteGuard'
import { ChatIndexRedirect, ChatPage } from '@/routes/pages/ChatPage'
import {
  DatabaseIndexRedirect,
  DatabaseTabGuard,
} from '@/routes/pages/DatabasePage'
import {
  KnowledgeBaseIndexPage,
  KnowledgeBasePage,
} from '@/routes/pages/KnowledgeBasePage'
import { MasteryIndexRedirect, MasteryTabGuard } from '@/routes/pages/MasteryPage'
import { QualityIndexRedirect, QualityTabGuard } from '@/routes/pages/QualityPage'
import { SettingsIndexRedirect, SettingsSectionGuard } from '@/routes/pages/SettingsPage'
import { UsageIndexRedirect, UsageTabGuard } from '@/routes/pages/UsagePage'

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatIndexRedirect />} />
        <Route path="/chat/:sessionId" element={<ChatPage />} />
        <Route path="/kb" element={<KnowledgeBaseIndexPage />} />
        <Route path="/kb/:alias" element={<KnowledgeBasePage />} />
        <Route path="/memory" element={<MemoryView />} />
        <Route path="/rules" element={<RulesView />} />
        <Route
          path="/skills"
          element={
            <RouteGuard>
              <SkillsView />
            </RouteGuard>
          }
        />
        <Route
          path="/mcp"
          element={
            <RouteGuard>
              <MCPView />
            </RouteGuard>
          }
        />
        <Route path="/mastery" element={<MasteryIndexRedirect />} />
        <Route path="/mastery/:tab" element={<MasteryTabGuard />} />
        <Route path="/usage" element={<UsageIndexRedirect />} />
        <Route
          path="/usage/:tab"
          element={
            <RouteGuard>
              <UsageTabGuard />
            </RouteGuard>
          }
        />
        <Route path="/quality" element={<QualityIndexRedirect />} />
        <Route
          path="/quality/:tab"
          element={
            <RouteGuard>
              <QualityTabGuard />
            </RouteGuard>
          }
        />
        <Route path="/database" element={<DatabaseIndexRedirect />} />
        <Route
          path="/database/:tab"
          element={
            <RouteGuard>
              <DatabaseTabGuard />
            </RouteGuard>
          }
        />
        <Route
          path="/backup"
          element={
            <RouteGuard>
              <BackupView />
            </RouteGuard>
          }
        />
        <Route path="/settings" element={<SettingsIndexRedirect />} />
        <Route
          path="/settings/:section"
          element={
            <RouteGuard>
              <SettingsSectionGuard />
            </RouteGuard>
          }
        />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  )
}
