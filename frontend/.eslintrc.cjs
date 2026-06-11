// Files grandfathered above the max-lines budget live in the ratchet file —
// one source of truth shared with scripts/ratchets/check_line_budgets.py,
// which enforces that they only ever shrink.
const lineBudgets = require('../scripts/ratchets/line_budgets.json')
const grandfatheredMaxLines = Object.keys(lineBudgets)
  .filter((path) => path.startsWith('frontend/'))
  .map((path) => path.replace(/^frontend\//, ''))

module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
    jest: true,
  },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: {
      jsx: true,
    },
  },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
  ],
  settings: {
    react: {
      version: 'detect',
    },
  },
  rules: {
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',
    'react/no-unescaped-entities': 'off',
    'react-refresh/only-export-components': 'warn',
    '@typescript-eslint/no-explicit-any': 'warn',
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': [
      'warn',
      {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      },
    ],
    // while (true) reader loops are idiomatic for SSE/stream consumption.
    'no-constant-condition': ['error', { checkLoops: false }],
    'max-lines': [
      'error',
      { max: 600, skipBlankLines: true, skipComments: true },
    ],
  },
  overrides: [
    {
      files: grandfatheredMaxLines,
      rules: {
        'max-lines': 'off',
      },
    },
    {
      files: [
        '**/*.test.ts',
        '**/*.test.tsx',
        '**/__tests__/**',
        'e2e/**',
        'jest.setup.ts',
      ],
      rules: {
        'max-lines': 'off',
        'react/display-name': 'off',
        '@typescript-eslint/no-require-imports': 'off',
      },
    },
  ],
  ignorePatterns: ['dist', 'node_modules', 'coverage', 'test-results', 'src/api/generated'],
}
