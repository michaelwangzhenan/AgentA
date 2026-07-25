export function ForbiddenView() {
  return (
    <div className="flex h-full flex-1 items-center justify-center p-6">
      <p className="max-w-md text-center text-sm text-muted-foreground">
        无权限访问此页面。如需体验完整功能，申请管理员权限。
      </p>
    </div>
  )
}
