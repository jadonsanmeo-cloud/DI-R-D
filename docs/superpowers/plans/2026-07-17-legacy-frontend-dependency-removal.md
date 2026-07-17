# Legacy Frontend Dependency Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove legacy GPTVis, old `/chat`, OceanBase/Monaco build plugins, syntax highlighting, and inactive connector confirmation from the `/` compilation graph.

**Architecture:** Preserve existing Markdown call sites through a lightweight compatibility wrapper, replace the old chat page with a redirect, retain route-local Monaco for workflow nodes, and make CodePreview a plain styled code block. A source-boundary script verifies that the retired dependencies cannot return to active route/config files.

**Tech Stack:** Next.js 13, React 18, TypeScript, Node.js source checks, `react-markdown`, `@monaco-editor/react` for the surviving workflow editor.

---

### Task 1: Extend the Dependency Boundary Test

**Files:**
- Modify: `web/scripts/check-manus-module-boundaries.mjs`

- [ ] Add checks that fail when `MarkdownContext.tsx` imports GPTVis/config, `pages/chat/index.tsx` imports old chat modules, `next.config.js` registers Monaco/OceanBase/GPTVis/syntax-highlighter build entries, `CodePreview` imports syntax-highlighter, or `pages/index.tsx` imports connector confirmation.
- [ ] Run `node scripts/check-manus-module-boundaries.mjs` and verify it fails for all current violations.

### Task 2: Replace Shared Markdown and Plain Code Preview

**Files:**
- Modify: `web/new-components/common/MarkdownContext.tsx`
- Modify: `web/components/chat/chat-content/code-preview.tsx`
- Modify: `web/new-components/chat/content/SpecConfirmationCard.tsx`

- [ ] Make `MarkdownContext` render `LightweightMarkdown` without GPTVis/config imports.
- [ ] Make `CodePreview` render `<pre><code>` while retaining its copy button, custom style, height, and overflow props.
- [ ] Import `LightweightMarkdown` directly in `SpecConfirmationCard` so confirmation no longer creates a GPTVis loadable chunk.
- [ ] Run the boundary check and focused ESLint.

### Task 3: Retire the Old Chat Route

**Files:**
- Replace: `web/pages/chat/index.tsx`

- [ ] Preserve `ChatContentContext` as a lightweight `createContext<Record<string, any>>({})` export for compatibility consumers.
- [ ] Replace the page UI with a client redirect to `/` using `router.replace('/')`.
- [ ] Verify the file has no imports of ChatContentContainer, DbEditor, ChatContainer, ChatCompletion, OpenCodeChatCompletion, or Monaco editor components.

### Task 4: Remove Global Monaco/OceanBase and Legacy Transpilation

**Files:**
- Modify: `web/next.config.js`

- [ ] Remove `copy-webpack-plugin`, `monaco-editor-webpack-plugin`, and `path` requires.
- [ ] Remove the client plugin block that copies OceanBase workers and registers MonacoWebpackPlugin.
- [ ] Remove `react-syntax-highlighter` and `@antv/gpt-vis` from `next-transpile-modules`.
- [ ] Keep `@monaco-editor/react`, `monaco-editor`, and the workflow node editor unchanged.

### Task 5: Remove Inactive Connector Confirmation

**Files:**
- Modify: `web/pages/index.tsx`
- Modify: `web/new-components/connector/index.ts`

- [ ] Remove `ConfirmDialog`, `useConfirmPolling`, the false activation flag, returned callbacks/state, and dialog JSX from the homepage.
- [ ] Remove only the two related barrel exports; preserve connector selection/types/API behavior.

### Task 6: Verify Module Reduction

**Files:**
- Verify all changed files.

- [ ] Run `node scripts/check-manus-module-boundaries.mjs` and expect exit `0`.
- [ ] Run focused ESLint on the changed files; separate pre-existing staged-file diagnostics.
- [ ] Run TypeScript and confirm no diagnostics reference changed files.
- [ ] Inspect `.next` after development recompilation: legacy MarkdownContext, old chat container/DB editor, OceanBase parser, and syntax-language chunks must no longer be reachable from `/`.
- [ ] Run `npm run build -- --no-lint`; record unrelated repository prerender blockers separately.
- [ ] Run `git diff --check` and verify user-owned staged changes remain intact.
