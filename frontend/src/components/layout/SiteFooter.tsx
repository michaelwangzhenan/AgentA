const ICP_NUMBER = '浙ICP备2026055936号-1'
const ICP_URL = 'https://beian.miit.gov.cn/'

export function SiteFooter() {
  return (
    <footer className="shrink-0 border-t border-border/60 bg-background py-2.5 text-center">
      <a
        href={ICP_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm text-muted-foreground hover:text-foreground hover:underline"
      >
        {ICP_NUMBER}
      </a>
    </footer>
  )
}
