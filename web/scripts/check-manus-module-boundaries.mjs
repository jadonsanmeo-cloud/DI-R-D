import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const paths = {
  left: path.join(webRoot, 'new-components/chat/content/ManusLeftPanel.tsx'),
  right: path.join(webRoot, 'new-components/chat/content/ManusRightPanel.tsx'),
  markdown: path.join(webRoot, 'new-components/chat/content/LightweightMarkdown.tsx'),
  markdownCompatibility: path.join(webRoot, 'new-components/common/MarkdownContext.tsx'),
  specConfirmation: path.join(webRoot, 'new-components/chat/content/SpecConfirmationCard.tsx'),
  codePreview: path.join(webRoot, 'components/chat/chat-content/code-preview.tsx'),
  sessionTurn: path.join(webRoot, 'new-components/chat/content/SessionTurn.tsx'),
  oldChatPage: path.join(webRoot, 'pages/chat/index.tsx'),
  playgroundPage: path.join(webRoot, 'pages/playground.tsx'),
  homePage: path.join(webRoot, 'pages/index.tsx'),
  nextConfig: path.join(webRoot, 'next.config.js'),
};

const errors = [];
const read = filePath => (fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : '');
const left = read(paths.left);
const right = read(paths.right);
const markdown = read(paths.markdown);
const markdownCompatibility = read(paths.markdownCompatibility);
const specConfirmation = read(paths.specConfirmation);
const codePreview = read(paths.codePreview);
const sessionTurn = read(paths.sessionTurn);
const oldChatPage = read(paths.oldChatPage);
const playgroundPage = read(paths.playgroundPage);
const homePage = read(paths.homePage);
const nextConfig = read(paths.nextConfig);

for (const [label, source] of [
  ['ManusLeftPanel', left],
  ['ManusRightPanel', right],
  ['LightweightMarkdown', markdown],
]) {
  if (source.includes('@antv/gpt-vis')) errors.push(`${label} imports @antv/gpt-vis`);
  if (source.includes('components/chat/chat-content/config')) {
    errors.push(`${label} imports the heavyweight chat Markdown configuration`);
  }
}

if (!left.includes('./LightweightMarkdown')) {
  errors.push('ManusLeftPanel does not use LightweightMarkdown');
}
if (!right.includes('./LightweightMarkdown')) {
  errors.push('ManusRightPanel does not use LightweightMarkdown');
}
if (!markdown) errors.push('LightweightMarkdown.tsx does not exist');

const staticCodePreview = /^import\s+\{?\s*CodePreview\b[^;]*from\s+['"]/m;
const staticAdvancedChart = /^import\s+AdvancedChart\b[^;]*from\s+['"]/m;
if (staticCodePreview.test(right)) errors.push('ManusRightPanel statically imports CodePreview');
if (staticAdvancedChart.test(right)) errors.push('ManusRightPanel statically imports AdvancedChart');

if (markdownCompatibility.includes('@antv/gpt-vis')) {
  errors.push('MarkdownContext imports @antv/gpt-vis');
}
if (markdownCompatibility.includes('components/chat/chat-content/config')) {
  errors.push('MarkdownContext imports the heavyweight chat Markdown configuration');
}
if (!markdownCompatibility.includes('LightweightMarkdown')) {
  errors.push('MarkdownContext does not delegate to LightweightMarkdown');
}
if (!specConfirmation.includes('LightweightMarkdown')) {
  errors.push('SpecConfirmationCard does not use LightweightMarkdown directly');
}
if (codePreview.includes('react-syntax-highlighter')) {
  errors.push('CodePreview imports react-syntax-highlighter');
}
if (sessionTurn.includes('@antv/gpt-vis') || sessionTurn.includes('components/chat/chat-content/config')) {
  errors.push('SessionTurn imports the heavyweight GPTVis Markdown path');
}
if (!sessionTurn.includes('LightweightMarkdown')) {
  errors.push('SessionTurn does not use LightweightMarkdown');
}
if (playgroundPage.includes("from '@/new-components/chat'")) {
  errors.push('The /playground page imports the legacy chat barrel');
}

for (const legacyImport of [
  'ChatContentContainer',
  'components/chat/db-editor',
  'components/chat/chat-container',
  'OpenCodeChatCompletion',
  'ChatCompletion',
]) {
  if (oldChatPage.includes(legacyImport)) errors.push(`The /chat page imports ${legacyImport}`);
}
if (!oldChatPage.includes("router.replace('/')")) {
  errors.push('The /chat page is not a lightweight redirect to /');
}

for (const forbiddenConfig of [
  'MonacoWebpackPlugin',
  'monaco-editor-webpack-plugin',
  'copy-webpack-plugin',
  'monaco-plugin-ob/worker-dist',
  '@antv/gpt-vis',
  'react-syntax-highlighter',
]) {
  if (nextConfig.includes(forbiddenConfig)) errors.push(`next.config.js still contains ${forbiddenConfig}`);
}

for (const inactiveConfirmation of ['ConfirmDialog', 'useConfirmPolling', 'isConfirmPollingActive']) {
  if (homePage.includes(inactiveConfirmation)) {
    errors.push(`The homepage still contains ${inactiveConfirmation}`);
  }
}

if (errors.length > 0) {
  console.error(errors.map(error => `- ${error}`).join('\n'));
  process.exit(1);
}

console.log('Manus module boundaries are lightweight.');
