const ICP_NUMBER = '浙ICP备2026055936号-1'
const ICP_URL = 'https://beian.miit.gov.cn/'
const PSB_NUMBER = '浙公网安备33010502013300号'
const PSB_URL = 'https://beian.mps.gov.cn/#/query/webSearch?code=33010502013300'

const linkClass =
  'text-sm text-muted-foreground hover:text-foreground hover:underline'

export function SiteFooter() {
  return (
    <footer className="shrink-0 border-t border-border/60 bg-background py-2.5 text-center">
      <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
        <a
          href={ICP_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={linkClass}
        >
          {ICP_NUMBER}
        </a>
        <a
          href={PSB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={`inline-flex items-center gap-1 ${linkClass}`}
        >
          <img
            src="/beian-icon.png"
            alt=""
            width={16}
            height={17}
            className="inline-block"
          />
          {PSB_NUMBER}
        </a>
      </div>
    </footer>
  )
}
