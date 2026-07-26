import type { ReactElement } from 'react'
import { Route } from 'react-router-dom'

import { ContactPage } from '@/components/public/ContactPage'
import { StaticMarkdownPage } from '@/components/public/StaticMarkdownPage'
import { STATIC_PAGE_ENTRIES } from '@/lib/staticPages'

export function getPublicRouteElements(): ReactElement[] {
  return [
    <Route key="/contact" path="/contact" element={<ContactPage />} />,
    ...STATIC_PAGE_ENTRIES.map((page) => (
      <Route
        key={page.path}
        path={page.path}
        element={<StaticMarkdownPage page={page} />}
      />
    )),
  ]
}
