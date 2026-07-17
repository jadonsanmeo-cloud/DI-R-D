# Index Page Module Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the GPTVis/AntV dependency graph from the Manus panels' base rendering path while preserving lightweight Markdown, math, code, tables, structured charts, and existing response behavior.

**Architecture:** Introduce a Manus-specific `react-markdown` renderer with no GPTVis dependency. Keep charting and rich syntax highlighting behind Next dynamic imports, and remove page-local code whose computed state has no rendered consumer. A source-boundary test prevents the heavyweight dependencies from returning to the base panel chunks.

**Tech Stack:** Next.js 13, React 18, TypeScript, `react-markdown`, `remark-gfm`, `remark-math`, `rehype-katex`, Next dynamic imports, Node.js validation scripts.

---

## File Map

- Create `web/scripts/check-manus-module-boundaries.mjs`: source-level regression test for forbidden heavyweight imports.
- Create `web/new-components/chat/content/LightweightMarkdown.tsx`: focused Markdown/GFM/math renderer used only by the Manus UI.
- Modify `web/new-components/chat/content/ManusLeftPanel.tsx`: replace the heavyweight shared Markdown renderer.
- Modify `web/new-components/chat/content/ManusRightPanel.tsx`: replace GPTVis Markdown and defer chart/code implementations.
- Modify `web/pages/index.tsx`: remove unrendered analysis state, effects, private helpers, and their imports.

Existing staged changes in these files are user-owned. Apply every edit to the current file contents and never restore a file from `HEAD`.

### Task 1: Add the Failing Dependency-Boundary Test

**Files:**
- Create: `web/scripts/check-manus-module-boundaries.mjs`

- [ ] **Step 1: Create the source-boundary test**

```js
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const paths = {
  left: path.join(webRoot, 'new-components/chat/content/ManusLeftPanel.tsx'),
  right: path.join(webRoot, 'new-components/chat/content/ManusRightPanel.tsx'),
  markdown: path.join(webRoot, 'new-components/chat/content/LightweightMarkdown.tsx'),
};

const errors = [];
const read = filePath => (fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : '');
const left = read(paths.left);
const right = read(paths.right);
const markdown = read(paths.markdown);

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

if (!left.includes("./LightweightMarkdown")) {
  errors.push('ManusLeftPanel does not use LightweightMarkdown');
}
if (!right.includes("./LightweightMarkdown")) {
  errors.push('ManusRightPanel does not use LightweightMarkdown');
}
if (!markdown) errors.push('LightweightMarkdown.tsx does not exist');

const staticCodePreview = /^import\s+\{?\s*CodePreview\b[^;]*from\s+['"]/m;
const staticAdvancedChart = /^import\s+AdvancedChart\b[^;]*from\s+['"]/m;
if (staticCodePreview.test(right)) errors.push('ManusRightPanel statically imports CodePreview');
if (staticAdvancedChart.test(right)) errors.push('ManusRightPanel statically imports AdvancedChart');

if (errors.length > 0) {
  console.error(errors.map(error => `- ${error}`).join('\n'));
  process.exit(1);
}

console.log('Manus module boundaries are lightweight.');
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
node scripts/check-manus-module-boundaries.mjs
```

Expected: exit code `1`, reporting the current `GPTVis`, heavyweight Markdown configuration, static `CodePreview`, static `AdvancedChart`, and missing `LightweightMarkdown` violations.

- [ ] **Step 3: Commit the failing test**

```bash
git add web/scripts/check-manus-module-boundaries.mjs
git commit -m "test: guard Manus module boundaries"
```

### Task 2: Add Lightweight Manus Markdown

**Files:**
- Create: `web/new-components/chat/content/LightweightMarkdown.tsx`
- Test: `web/scripts/check-manus-module-boundaries.mjs`

- [ ] **Step 1: Implement the focused renderer**

```tsx
import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

const components: Components = {
  a: ({ children, ...props }) => (
    <a {...props} target='_blank' rel='noreferrer' className='text-blue-600 hover:underline dark:text-blue-400'>
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className='my-3 overflow-x-auto'>
      <table className='min-w-full border-collapse text-sm'>{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className='border border-gray-200 bg-gray-50 px-3 py-2 text-left font-semibold dark:border-gray-700 dark:bg-gray-800'>
      {children}
    </th>
  ),
  td: ({ children }) => <td className='border border-gray-200 px-3 py-2 dark:border-gray-700'>{children}</td>,
  pre: ({ children }) => (
    <pre className='my-3 overflow-x-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100'>{children}</pre>
  ),
  code: ({ children, className, ...props }) => (
    <code
      {...props}
      className={className || 'rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.9em] dark:bg-gray-800'}
    >
      {children}
    </code>
  ),
};

export const preprocessManusMath = (value: string): string => {
  const codeBlocks: string[] = [];
  const protectedValue = value.replace(/(```[\s\S]*?```|`[^`\n]+`)/g, match => {
    codeBlocks.push(match);
    return `<<MANUS_CODE_BLOCK_${codeBlocks.length - 1}>>`;
  });

  return protectedValue
    .replace(/\\\\\[/g, '$$')
    .replace(/\\\\\]/g, '$$')
    .replace(/\\\\\(/g, '$')
    .replace(/\\\\\)/g, '$')
    .replace(/\\\[/g, '$$')
    .replace(/\\\]/g, '$$')
    .replace(/\\\(/g, '$')
    .replace(/\\\)/g, '$')
    .replace(/([^\n])\$\$/g, '$1\n\n$$')
    .replace(/\$\$([^\n])/g, '$$\n\n$1')
    .replace(/\$(?=\d)/g, '\\$')
    .replace(/<<MANUS_CODE_BLOCK_(\d+)>>/g, (_match, index: string) => codeBlocks[Number(index)]);
};

const LightweightMarkdown = ({ children }: { children: string }) => (
  <ReactMarkdown
    components={components}
    remarkPlugins={[remarkGfm, remarkMath]}
    rehypePlugins={[rehypeKatex]}
  >
    {preprocessManusMath(children)}
  </ReactMarkdown>
);

export default LightweightMarkdown;
```

Do not add `rehype-raw`; HTML artifacts use their existing isolated preview.

- [ ] **Step 2: Run the boundary test**

Run:

```bash
node scripts/check-manus-module-boundaries.mjs
```

Expected: still FAIL because the two panels have not switched imports and the right panel still has static rich-renderer imports. The missing-file error must be gone.

- [ ] **Step 3: Commit the renderer**

```bash
git add web/new-components/chat/content/LightweightMarkdown.tsx
git commit -m "feat: add lightweight Manus markdown renderer"
```

### Task 3: Remove GPTVis from the Manus Panels

**Files:**
- Modify: `web/new-components/chat/content/ManusLeftPanel.tsx`
- Modify: `web/new-components/chat/content/ManusRightPanel.tsx`
- Test: `web/scripts/check-manus-module-boundaries.mjs`

- [ ] **Step 1: Switch the left panel to LightweightMarkdown**

Replace:

```tsx
import MarkdownContext from '@/new-components/common/MarkdownContext';
```

with:

```tsx
import LightweightMarkdown from './LightweightMarkdown';
```

Replace the assistant answer renderer:

```tsx
<MarkdownContext>{assistantText}</MarkdownContext>
```

with:

```tsx
<LightweightMarkdown>{assistantText}</LightweightMarkdown>
```

- [ ] **Step 2: Replace the right panel's heavyweight imports**

Remove the static imports of `CodePreview`, the shared chat Markdown configuration, `AdvancedChart`, `createChartConfig`, `MarkDownContext`, and `GPTVis`. Add:

```tsx
import type { ChartConfig } from '@/new-components/charts/types';
import dynamic from 'next/dynamic';
import LightweightMarkdown from './LightweightMarkdown';
```

Define the optional renderers after imports:

```tsx
const CodePreview = dynamic(
  () => import('@/components/chat/chat-content/code-preview').then(module => module.CodePreview),
  {
    ssr: false,
    loading: () => <div className='h-24 animate-pulse rounded-lg bg-gray-100 dark:bg-gray-800' />,
  },
);

const AdvancedChart = dynamic(() => import('@/new-components/charts/AdvancedCharts'), {
  ssr: false,
  loading: () => <div className='h-72 animate-pulse rounded-lg bg-gray-100 dark:bg-gray-800' />,
});

const createManusChartConfig = (content: any): ChartConfig => ({
  chartType: content?.chartType || 'line',
  data: content?.data || [],
  xField: content?.xField || 'x',
  yField: content?.yField || 'y',
  seriesField: content?.seriesField,
  colorField: content?.colorField,
  angleField: content?.angleField,
  title: content?.title,
  smooth: true,
  autoFit: true,
  height: 280,
  showLegend: true,
  showGrid: true,
  animate: true,
  enableZoom: true,
  enableTooltipCrosshairs: true,
  showToolbar: true,
  enableFullscreen: true,
});
```

- [ ] **Step 3: Switch all right-panel Markdown call sites**

Replace the Markdown output block with:

```tsx
{output.output_type === 'markdown' && (
  <div className='prose prose-sm dark:prose-invert max-w-none'>
    <LightweightMarkdown>{String(content)}</LightweightMarkdown>
  </div>
)}
```

Replace every remaining `MarkDownContext` use in skill files and summary rendering with `LightweightMarkdown`. Do not change the dedicated HTML preview path.

- [ ] **Step 4: Use the lightweight chart config**

Replace:

```tsx
<AdvancedChart config={createChartConfig(content?.data || [], options)} />
```

with:

```tsx
<AdvancedChart config={createManusChartConfig(content)} />
```

- [ ] **Step 5: Run the boundary test and verify GREEN**

Run:

```bash
node scripts/check-manus-module-boundaries.mjs
```

Expected:

```text
Manus module boundaries are lightweight.
```

- [ ] **Step 6: Commit the panel changes**

```bash
git add web/new-components/chat/content/ManusLeftPanel.tsx web/new-components/chat/content/ManusRightPanel.tsx
git commit -m "perf: defer heavy Manus renderers"
```

### Task 4: Remove Dead Page-Local Dependency Paths

**Files:**
- Modify: `web/pages/index.tsx`
- Test: `web/scripts/check-manus-module-boundaries.mjs`

- [ ] **Step 1: Remove imports used only by dead code**

Remove these imports after confirming with `rg` that their only references are the private helpers/state listed below:

```tsx
import type { PreprocessingResult } from '@/new-components/analysis/DataPreprocessor';
import type { ColumnAnalysis } from '@/new-components/analysis/core';
import { analyzeDataset } from '@/new-components/analysis/core';
import type { MessagePart, ToolPart, ToolStatus } from '@/new-components/chat/content/OpenCodeSessionTurn';
```

Remove `BarChartOutlined`, `CodeOutlined`, `PieChartOutlined`, and `TableOutlined` from the icon import only if `rg` shows they have no live reference after helper deletion.

- [ ] **Step 2: Convert the remaining page icons to direct imports**

Replace the `@ant-design/icons` barrel import with direct imports for the icons that remain live after Step 1:

```tsx
import ApiOutlined from '@ant-design/icons/ApiOutlined';
import ArrowUpOutlined from '@ant-design/icons/ArrowUpOutlined';
import BookOutlined from '@ant-design/icons/BookOutlined';
import CheckCircleFilled from '@ant-design/icons/CheckCircleFilled';
import CloudServerOutlined from '@ant-design/icons/CloudServerOutlined';
import ConsoleSqlOutlined from '@ant-design/icons/ConsoleSqlOutlined';
import DatabaseOutlined from '@ant-design/icons/DatabaseOutlined';
import FileExcelOutlined from '@ant-design/icons/FileExcelOutlined';
import FileImageOutlined from '@ant-design/icons/FileImageOutlined';
import FileOutlined from '@ant-design/icons/FileOutlined';
import FilePptOutlined from '@ant-design/icons/FilePptOutlined';
import FileTextOutlined from '@ant-design/icons/FileTextOutlined';
import LeftOutlined from '@ant-design/icons/LeftOutlined';
import PaperClipOutlined from '@ant-design/icons/PaperClipOutlined';
import PlusOutlined from '@ant-design/icons/PlusOutlined';
import ReadOutlined from '@ant-design/icons/ReadOutlined';
import RightOutlined from '@ant-design/icons/RightOutlined';
import ThunderboltOutlined from '@ant-design/icons/ThunderboltOutlined';
import UploadOutlined from '@ant-design/icons/UploadOutlined';
```

Before adding an import, verify the symbol still has a live reference. Omit any symbol removed by the user's current staged edits.

- [ ] **Step 3: Remove dead declarations and effects**

Delete the complete declarations for:

```tsx
const _convertExecutionToMessageParts = ...;
const [_rightPanelTab, setRightPanelTab] = useState<RightPanelTab>('preview');
const [_dataAnalysis, setDataAnalysis] = useState<ColumnAnalysis[] | null>(null);
const [_analysisLoading, setAnalysisLoading] = useState(false);
const [_showProfessionalReport, _setShowProfessionalReport] = useState(false);
const [_preprocessedData, _setPreprocessedData] = useState<PreprocessingResult | null>(null);
const parseCsvLine = ...;
const _parseCsvText = ...;
const _copyToClipboard = ...;
const _getArtifactIcon = ...;
const _QuickAction = ...;
```

Delete the auto-analysis `useEffect` that calls `analyzeDataset`. Remove all `setRightPanelTab(...)` calls because the state value has no renderer. Keep `rightPanelView` and its setters; that state controls the visible right panel.

- [ ] **Step 4: Confirm no deleted symbol remains**

Run:

```bash
rg -n "PreprocessingResult|ColumnAnalysis|analyzeDataset|MessagePart|ToolPart|ToolStatus|_convertExecutionToMessageParts|setRightPanelTab|_dataAnalysis|_analysisLoading|_showProfessionalReport|_preprocessedData|parseCsvLine|_parseCsvText|_copyToClipboard|_getArtifactIcon|_QuickAction" pages/index.tsx
```

Expected: no output.

- [ ] **Step 5: Run the existing upload regression check**

Run:

```bash
npm run check:qa-upload
```

Expected: exit code `0` and the backend QA upload assertions pass.

- [ ] **Step 6: Commit the page cleanup**

```bash
git add web/pages/index.tsx
git commit -m "perf: remove unused index page analysis paths"
```

### Task 5: Verify Build and Module Separation

**Files:**
- Verify: `web/pages/index.tsx`
- Verify: `web/new-components/chat/content/ManusLeftPanel.tsx`
- Verify: `web/new-components/chat/content/ManusRightPanel.tsx`
- Verify: `web/new-components/chat/content/LightweightMarkdown.tsx`
- Verify: `web/scripts/check-manus-module-boundaries.mjs`

- [ ] **Step 1: Run the source boundary and upload checks**

```bash
node scripts/check-manus-module-boundaries.mjs
npm run check:qa-upload
```

Expected: both commands exit `0`.

- [ ] **Step 2: Run ESLint without auto-fixing unrelated files**

```bash
npx eslint pages/index.tsx new-components/chat/content/ManusLeftPanel.tsx new-components/chat/content/ManusRightPanel.tsx new-components/chat/content/LightweightMarkdown.tsx
```

Expected: no new errors in the changed files. Record pre-existing errors separately instead of editing unrelated code.

- [ ] **Step 3: Run the production build**

```bash
npm run build
```

Expected: Next completes the production build. The project currently ignores TypeScript build errors, so inspect the final route-size table and any warnings rather than treating ignored pre-existing type errors as new failures.

- [ ] **Step 4: Inspect emitted dynamic chunks**

```bash
node -e "const fs=require('fs'); const m=JSON.parse(fs.readFileSync('.next/react-loadable-manifest.json','utf8')); for (const [k,v] of Object.entries(m)) if (/Manus|AdvancedCharts|code-preview|LightweightMarkdown/.test(k)) console.log(k, v.files)"
```

Expected: `AdvancedCharts` and `code-preview` are emitted as optional dynamic chunks rather than static imports of `ManusRightPanel`.

- [ ] **Step 5: Review the final diff for user-change preservation**

```bash
git diff --check
git status --short
git diff -- web/pages/index.tsx web/new-components/chat/content/ManusLeftPanel.tsx web/new-components/chat/content/ManusRightPanel.tsx web/new-components/chat/content/LightweightMarkdown.tsx web/scripts/check-manus-module-boundaries.mjs
```

Expected: no whitespace errors, no reverted sidebar work, and only the scoped module-reduction edits in the implementation diff.
