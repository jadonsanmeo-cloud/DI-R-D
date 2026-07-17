# Legacy Frontend Dependency Removal Design

## Goal

Reduce the Next.js development compilation graph by removing legacy GPTVis, old `/chat`, OceanBase SQL-editor, syntax-highlighting, and inactive connector-confirmation paths while preserving the current `/` Manus experience, structured charts, management pages, mobile chat, and workflow code editing.

## Current Evidence

The `/` development compilation reports about 12,131 modules. Chunk inspection shows the largest optional or legacy groups:

- Legacy GPTVis and visual-chat chunks: about 3,745 module occurrences.
- Old chat container and database editor: about 2,601 module occurrences.
- Old ChatCompletion/OpenCode variants: about 1,562 module occurrences within the legacy graph.
- Monaco/OceanBase generated chunks: about 367 module occurrences, including a roughly 360 MB development parser chunk.
- Syntax-highlighter language chunks: about 336 module occurrences, plus the code-preview base.

The current Manus panels and structured chart renderer remain in scope and must continue working.

## Scope

### Lightweight Markdown Everywhere Reachable

`new-components/common/MarkdownContext.tsx` will become a compatibility wrapper around `LightweightMarkdown`. Existing management, prompt, skill, knowledge, mobile, reference, and confirmation screens can keep their current imports without reaching GPTVis.

Surviving directly imported GPTVis renderers will switch to `LightweightMarkdown`. Visual-only GPTVis components that are reachable only from the retired old-chat configuration will be detached from active routes. The `@antv/gpt-vis` transpilation entry will be removed. The package will be removed only after a source scan confirms there are no remaining imports; otherwise it may remain installed but unreachable until the dead source files are deleted separately.

GPTVis-specific custom tags such as `vis-dashboard`, `vis-plugin`, `agent-plans`, and embedded chart code will no longer render. Normal Markdown, GFM tables, links, code blocks, and KaTeX math remain.

### Retire the Old `/chat` Application

`pages/chat/index.tsx` will become a lightweight redirect to `/` so old bookmarks do not produce a broken page.

The reusable `ChatContentContext` contract currently exported from the page will move to a small non-page module for mobile or surviving compatibility consumers. Those consumers will import the new context module instead of importing a Next page.

The redirect must not import the old chat container, database editor, ChatCompletion/OpenCode completion modes, Monaco SQL editor, or legacy visualization configuration. These components may remain as unreachable source temporarily if deleting them would broaden the change, but they must no longer participate in the route compilation graph.

### Remove OceanBase and Global Monaco Build Machinery

Remove `MonacoWebpackPlugin` and the OceanBase worker-copy configuration from `next.config.js`. Remove the OceanBase plugin integration and its package when it has no surviving imports.

Keep `@monaco-editor/react` and `monaco-editor` because the workflow node code editor still uses them. The workflow editor remains route-local and must not force Monaco/OceanBase language workers into `/` compilation.

The old database chat editor and SQL autocomplete are intentionally removed.

### Plain Code Preview

`CodePreview` will retain its public props, copy button, maximum height, overflow behavior, and custom styles, but render a styled `<pre><code>` instead of `react-syntax-highlighter`.

Remove `react-syntax-highlighter`, its type package, and its `next-transpile-modules` entry after confirming there are no remaining direct style imports. Legacy visual files that import syntax-highlighter themes must either stop importing them or become unreachable/deleted.

### Remove Inactive Connector Confirmation

Remove `ConfirmDialog`, `useConfirmPolling`, the permanently false activation flag, and the related render path from `pages/index.tsx`. Remove connector barrel exports for those two items. Connector selection and attachment remain available; only the disabled write-confirmation polling infrastructure is removed.

## Preserved Behavior

- Current `/` responses workflow, SSE processing, spec confirmation, file uploads, database/knowledge selection, connectors, scheduling, and artifacts.
- Manus Markdown, GFM, math, tables, HTML/image previews, plain code previews, and structured charts.
- Workflow-node code editing through `@monaco-editor/react`.
- Mobile chat and management pages that consume the compatibility Markdown component.
- Old `/chat` links redirect safely to `/`.

## Dependency Boundaries

Automated source checks will fail if:

- Reachable Markdown compatibility components import GPTVis or the heavyweight old chat configuration.
- `pages/chat/index.tsx` imports legacy chat containers, DB editors, or completion modes.
- `next.config.js` registers MonacoWebpackPlugin, OceanBase worker copying, GPTVis transpilation, or syntax-highlighter transpilation.
- `CodePreview` imports `react-syntax-highlighter`.
- `pages/index.tsx` imports or renders connector confirmation polling/dialog components.

## Verification

- Establish a failing dependency-boundary test before implementation.
- Verify the old `/chat` page is a lightweight redirect and mobile/context imports resolve.
- Run focused ESLint on changed files.
- Run TypeScript and report unrelated repository diagnostics separately.
- Compile `/` in development and compare the module count against 12,131.
- Inspect emitted chunks to confirm legacy GPTVis, old-chat/DB-editor, OceanBase parser, and syntax-language chunks are absent from the `/` compilation.
- Run the production compiler; record existing repository-wide lint or prerender blockers without modifying unrelated files.

## Success Criteria

- `/` no longer compiles the legacy GPTVis visual-chat graph.
- `/` no longer compiles the old chat container, DB editor, ChatCompletion/OpenCode variants, or OceanBase parser workers.
- Syntax-highlighter language chunks disappear.
- The current Manus page, structured charts, lightweight Markdown/math, workflow editor, and connector selection still compile.
- User-owned staged changes remain intact and are not reverted or mixed into implementation commits.
