module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  testPathIgnorePatterns: ['/node_modules/', '/dist/', '/e2e/'],
  roots: ['<rootDir>/src'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    // Stub CSS imports (e.g. `reactflow/dist/style.css`) — jsdom can't
    // parse them and Jest doesn't transform node_modules CSS by default.
    '\\.(css|less|scss|sass)$': '<rootDir>/src/__mocks__/styleMock.cjs',
  },
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/types/**',
    '!src/vite-env.d.ts',
    '!src/main.tsx',
  ],
  coverageReporters: ['text', 'text-summary', 'lcov', 'clover'],
}
