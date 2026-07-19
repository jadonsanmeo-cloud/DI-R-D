module.exports = {
  printWidth: 120,
  tabWidth: 2,
  useTabs: false,
  endOfLine: 'auto',
  semi: true,
  singleQuote: true,
  jsxSingleQuote: true,
  trailingComma: "all",
  bracketSpacing: true,
  jsxBracketSameLine: false,
  arrowParens: "avoid",
  plugins: [
    require.resolve('prettier-plugin-organize-imports'),
    require.resolve('prettier-plugin-packagejson'),
  ]
}
