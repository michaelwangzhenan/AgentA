import type { ButtonHTMLAttributes, ReactElement } from 'react'
import { cloneElement } from 'react'

import { useAuth } from '@/lib/auth'
import { writeDeniedMessage } from '@/lib/permissions'
import type { PermissionScope } from '@/types/auth'

type WriteGuardProps = {
  scope: PermissionScope
  children: ReactElement<ButtonHTMLAttributes<HTMLButtonElement>>
}

/** 无写权限时包裹子元素：disabled + title 提示；有权限时原样渲染。 */
export function WriteGuard({ scope, children }: WriteGuardProps) {
  const { canWrite, user } = useAuth()
  if (canWrite(scope)) return children

  const message = writeDeniedMessage(scope, user?.role)
  return cloneElement(children, {
    disabled: true,
    title: message,
    'aria-disabled': true,
  })
}
