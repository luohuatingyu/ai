/*
 * ESLint 配置（此前项目缺少该文件导致 npm run lint 无法执行）。
 * - vue-eslint-parser 解析 .vue 单文件组件，script 内容交给 TS 解析器；
 * - 规则取向：vue3-essential + eslint:recommended 基础约束，
 *   不引入过度风格化规则，避免干扰存量页面的增量改造；
 * - auto-import 全局变量由 unplugin 生成的 .eslintrc-auto-import.json 兜底。
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  extends: ['./.eslintrc-auto-import.json', 'plugin:vue/vue3-essential', 'eslint:recommended'],
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    ecmaVersion: 'latest',
    sourceType: 'module',
    extraFileExtensions: ['.vue'],
  },
  plugins: ['@typescript-eslint'],
  rules: {
    // 类型检查由 vue-tsc 负责，这里仅保留基础 TS 规则
    'no-undef': 'off',
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    'vue/multi-word-component-names': 'off',
    'vue/no-v-html': 'off',
  },
  ignorePatterns: ['dist/**', 'node_modules/**', 'tests/e2e/**', '*.d.ts'],
}
