import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // 业界标准：`_` 前缀的参数 / 变量视为"故意不用"，不报 unused-vars
      // 适用场景：第三方 API 类型签名要求带某参数但实际不用（如 ReactMarkdown 的 `node`）
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      // React 19 / eslint-plugin-react-hooks v5 新规则：effect 里同步 setState 会报错
      // 但 "组件挂载时 fetch 一次后 setState" 是合法用法、被规则误报
      // 社区主流处理是关掉（参见 facebook/react#30802）
      'react-hooks/set-state-in-effect': 'off',
    },
  },
  {
    // shadcn/ui 组件目录 + Context Provider 文件天然会 export 组件 + 常量/工具函数
    // 跟 react-refresh 的"only-export-components"假设不符；shadcn 官方模板也是这样
    files: ['src/components/ui/**', 'src/lib/theme.tsx'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
