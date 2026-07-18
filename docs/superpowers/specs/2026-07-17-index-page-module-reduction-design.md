# Index Page Module Reduction Design

## Goal

Reduce the module graph and initial client payload for `web/pages/index.tsx` without removing the page's core chat, file, Markdown, code, table, math, or structured-chart behavior.

The current development chunks show that the page chunk contains about 655 modules, while the dynamically loaded Manus panels contain roughly 1,900-2,100 modules each. About 1,266 modules in each panel come from the AntV graph reached through `GPTVis` and the shared heavyweight chat Markdown configuration. The global Monaco webpack configuration is a separate application-wide concern and is outside this page-scoped change.

## Scope

This change will:

- Replace the Manus panels' GPTVis-based Markdown path with a lightweight renderer.
- Keep GFM Markdown, links, lists, tables, fenced code, and KaTeX math.
- Keep structured chart outputs, but load the chart implementation only when a chart is rendered.
- Keep rich code previews, but load syntax highlighting only when a code or JSON output is rendered.
- Remove page-local analysis state, effects, helpers, and imports only when they have no rendered consumer.
- Reduce Ant Design barrel-import expansion in the page-scoped files where direct imports are safe.
- Add an automated dependency-boundary check for the heavy modules removed from the Manus path.

This change will not:

- Remove structured chart output support.
- Change the responses API, SSE event handling, uploads, scheduling, confirmation, history, or artifact behavior.
- Change unrelated sidebar or layout work already staged by the user.
- Remove Monaco or alter global editor behavior.
- Refactor the complete 3,600-line page beyond code directly related to unused dependencies.

## Architecture

### Lightweight Markdown Renderer

Add a small Manus-specific Markdown component that uses `react-markdown` with `remark-gfm`, `remark-math`, and `rehype-katex`. It will provide local styling for headings, paragraphs, links, tables, inline code, and fenced code.

Raw HTML inside Markdown will not be executed. HTML artifacts already have a dedicated preview path, so disabling raw Markdown HTML avoids pulling in the broader GPTVis configuration and preserves a clearer security boundary.

`ManusLeftPanel` will use this component for the assistant answer. `ManusRightPanel` will use it for Markdown outputs, summaries, and skill-file Markdown rendering.

### Deferred Rich Renderers

`ManusRightPanel` will dynamically import the chart and rich code-preview components with small loading placeholders.

The chart configuration object will be created in the panel or in a lightweight helper that imports only chart types. The panel must not statically import the chart barrel or `AdvancedCharts.tsx`, because importing `createChartConfig` from that module also pulls `@ant-design/plots` into the panel chunk.

The existing table renderer remains based on Ant Design Table because table interaction is core behavior and substantially smaller than the AntV visualization graph.

### Page Cleanup

Remove code from `pages/index.tsx` only when evidence shows it is unused:

- Unrendered preprocessing and data-analysis state.
- The `analyzeDataset` effect when its result has no consumer.
- Private helpers whose names and references show they are never called.
- Type-only imports associated exclusively with removed code.
- Icons used exclusively by removed helpers.

File preview and client-generated preview outputs remain because they feed the execution panel and artifact experience.

## Data Flow

```text
assistant text / Markdown output
  -> LightweightMarkdown
  -> react-markdown + GFM + math

code or JSON output
  -> dynamic CodePreview chunk

structured chart output
  -> lightweight config object
  -> dynamic AdvancedChart chunk

HTML artifact
  -> existing isolated HTML preview
```

The response event model and state shape do not change.

## Error Handling

- Dynamic chart and code components show a compact loading state while their chunks load.
- A failed optional renderer must not prevent the rest of the execution panel from rendering.
- Unsupported Markdown extensions fall back to visible text/code rather than disappearing.
- Existing chart, table, HTML, image, and file error behavior remains unchanged.

## Verification

Add a Node-based dependency-boundary check that fails before implementation and passes afterward. It will assert that:

- `ManusLeftPanel.tsx` and `ManusRightPanel.tsx` do not import `@antv/gpt-vis`.
- The panels do not import the heavyweight shared chat Markdown configuration.
- `ManusRightPanel.tsx` does not statically import `AdvancedChart` or `CodePreview`.
- The new lightweight Markdown component does not import GPTVis or the heavyweight configuration.

Then run:

- The dependency-boundary check.
- The existing TypeScript/build command, noting unrelated pre-existing type errors if the project continues to ignore them.
- A production Next build and inspect the `/` page chunks.
- A source/chunk module count comparison showing that the Manus left-panel chunk no longer contains the AntV graph and that charting is emitted separately.

## Success Criteria

- The Manus left-panel chunk has no AntV/GPTVis modules.
- The Manus right-panel base chunk has no static GPTVis dependency and does not eagerly contain charting or syntax-highlighting implementation modules.
- Ordinary Markdown, GFM tables, math, code, JSON, data tables, structured charts, summaries, and HTML artifacts still render through their intended paths.
- No unrelated user changes are reverted or included in the implementation.
