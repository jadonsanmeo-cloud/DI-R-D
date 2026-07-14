const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function loadTypeScriptModule(relativePath) {
  const filename = path.resolve(__dirname, '..', relativePath);
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  const execute = new Function('module', 'exports', 'require', '__filename', '__dirname', output);
  execute(module, module.exports, require, filename, path.dirname(filename));
  return module.exports;
}

const { ResponsesSSEParser, applyResponseEvent, createPipelineStages } = loadTypeScriptModule('utils/responses-sse.ts');
const { prepareResponseSubmission } = loadTypeScriptModule('utils/responses-request.ts');
const { parseSpecMarkdown, specToMarkdown } = loadTypeScriptModule('utils/spec-markdown.ts');

function assistantMessage() {
  return {
    id: 'assistant-1',
    role: 'assistant',
    content: '',
    status: 'streaming',
    stages: createPipelineStages(),
  };
}

function testSplitChunkParsing() {
  const parser = new ResponsesSSEParser();
  assert.deepEqual(parser.push('event: response.output_text.del'), []);
  const events = parser.push('ta\ndata: {"type":"response.output_text.delta","response_id":"resp_1","delta":"Hi"}\n\n');
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'response.output_text.delta');
  assert.equal(events[0].delta, 'Hi');
}

function testMultipleEventsInOneChunk() {
  const parser = new ResponsesSSEParser();
  const events = parser.push(
    'event: pipeline.start\ndata: {"type":"pipeline.start","response_id":"resp_1"}\n\n' +
      'event: pipeline.intent_analyzed\ndata: {"type":"pipeline.intent_analyzed","response_id":"resp_1"}\n\n',
  );
  assert.deepEqual(
    events.map(event => event.type),
    ['pipeline.start', 'pipeline.intent_analyzed'],
  );
}

function testMalformedRecordIsIgnored() {
  const parser = new ResponsesSSEParser();
  assert.deepEqual(parser.push('event: bad\ndata: {not-json}\n\n'), []);
}

function testUnknownEventIsIgnored() {
  const parser = new ResponsesSSEParser();
  assert.deepEqual(parser.push('event: future.event\ndata: {"type":"future.event","response_id":"resp_1"}\n\n'), []);
}

function testDeltaAndDoneTextReduction() {
  let message = assistantMessage();
  message = applyResponseEvent(message, {
    type: 'response.output_text.delta',
    response_id: 'resp_1',
    delta: 'Hel',
  });
  message = applyResponseEvent(message, {
    type: 'response.output_text.delta',
    response_id: 'resp_1',
    delta: 'lo',
  });
  assert.equal(message.content, 'Hello');
  message = applyResponseEvent(message, {
    type: 'response.output_text.done',
    response_id: 'resp_1',
    text: 'Hello world',
  });
  assert.equal(message.content, 'Hello world');
}

function testPipelineProgressionAndCompletion() {
  let message = assistantMessage();
  message = applyResponseEvent(message, {
    type: 'pipeline.intent_analyzed',
    response_id: 'resp_1',
  });
  assert.equal(message.stages[0].status, 'completed');
  assert.equal(message.stages[1].status, 'running');
  message = applyResponseEvent(message, {
    type: 'response.completed',
    response_id: 'resp_1',
    response: { id: 'resp_1', status: 'completed', output_text: 'Answer' },
    evidence: { sources: ['data/data.csv'] },
    metadata: { engine_name: 'fake' },
  });
  assert.equal(message.status, 'completed');
  assert.equal(message.content, 'Answer');
  assert.ok(message.stages.every(stage => stage.status === 'completed'));
}

function testFailureMarksCurrentStage() {
  let message = assistantMessage();
  message = applyResponseEvent(message, {
    type: 'pipeline.spec_built',
    response_id: 'resp_1',
  });
  message = applyResponseEvent(message, {
    type: 'response.failed',
    response_id: 'resp_1',
    response: { id: 'resp_1', status: 'failed' },
    error: { code: 'pipeline_execution_failed', message: 'Workflow failed.' },
  });
  assert.equal(message.status, 'failed');
  assert.equal(message.error, 'Workflow failed.');
  assert.equal(message.stages[2].status, 'failed');
  assert.equal(message.stages[3].status, 'pending');
}

function testEarlyFailureMarksFirstPendingStage() {
  const message = applyResponseEvent(assistantMessage(), {
    type: 'response.failed',
    response_id: 'resp_1',
    response: { id: 'resp_1', status: 'failed' },
    error: { code: 'pipeline_execution_failed', message: 'Workflow failed early.' },
  });
  assert.equal(message.stages[0].status, 'failed');
}

function testConfirmationPausesAtBuiltSpec() {
  const message = applyResponseEvent(assistantMessage(), {
    type: 'response.requires_confirmation',
    response_id: 'resp_1',
    revision: 1,
    confirmation_token: 'token-1',
    intent: { value: 'reason' },
    spec: {
      intent: 'reason',
      objective: 'Analyze revenue',
      data_requirements: ['data/data.csv'],
      capability_requirements: [{ name: 'inspect_data' }],
      constraints: {},
      confirmed: false,
      engine_hint: null,
    },
    expires_at: '2026-07-15T00:00:00+00:00',
  });
  assert.equal(message.status, 'awaiting_confirmation');
  assert.equal(message.responseId, 'resp_1');
  assert.equal(message.confirmation.revision, 1);
  assert.equal(message.confirmation.spec.objective, 'Analyze revenue');
  assert.equal(message.stages[2].status, 'completed');
  assert.equal(message.stages[3].status, 'pending');
}

function testConfirmationEventParsesFromStream() {
  const parser = new ResponsesSSEParser();
  const events = parser.push(
    'event: response.requires_confirmation\ndata: {"type":"response.requires_confirmation","response_id":"resp_1","revision":1,"confirmation_token":"token-1","intent":{"value":"reason"},"spec":{"intent":"reason","objective":"Analyze","data_requirements":[],"capability_requirements":[],"constraints":{},"confirmed":false},"expires_at":"2026-07-15T00:00:00Z"}\n\n',
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'response.requires_confirmation');
}

function testBlankQueryUsesVisibleFallbackAndOmitsApiInput() {
  const submission = prepareResponseSubmission('   ', [' data/data.csv '], 'session-1');
  assert.equal(submission.visibleInput, 'Analyze this data corpus.');
  assert.deepEqual(submission.request, {
    data_corpus_package: { sources: ['data/data.csv'], schemas: {}, metadata: {} },
    session_id: 'session-1',
  });
}

function testEmptySourcesRejectSubmission() {
  assert.equal(prepareResponseSubmission('question', [' ', ''], 'session-1'), null);
}

function testSpecMarkdownRoundTrip() {
  const spec = {
    intent: 'reason',
    objective: 'Analyze monthly revenue',
    data_requirements: ['data/data.csv'],
    capability_requirements: [
      {
        name: 'aggregate_data',
        description: 'Aggregate revenue by month',
        input_schema: {},
        output_schema: {},
        constraints: {},
        metadata: {},
      },
    ],
    constraints: { currency: 'USD', include_chart: true },
    confirmed: false,
    engine_hint: 'general_purpose',
  };
  const markdown = specToMarkdown(spec);
  assert.match(markdown, /# Analysis Plan/);
  assert.match(markdown, /## Objective/);
  assert.doesNotMatch(markdown, /```json/);

  const parsed = parseSpecMarkdown(markdown, spec);
  assert.equal(parsed.objective, spec.objective);
  assert.deepEqual(parsed.data_requirements, spec.data_requirements);
  assert.equal(parsed.capability_requirements[0].name, 'aggregate_data');
  assert.deepEqual(parsed.constraints, spec.constraints);
  assert.equal(parsed.engine_hint, 'general_purpose');
}

function testEditedMarkdownUpdatesStructuredSpec() {
  const original = {
    intent: 'reason',
    objective: 'Analyze revenue',
    data_requirements: ['old.csv'],
    capability_requirements: [],
    constraints: {},
    confirmed: false,
    engine_hint: null,
  };
  const edited = `# Analysis Plan

## Objective
Compare monthly revenue and costs.

## Data
- data/finance.csv

## Workflow
- **aggregate_monthly**: Group metrics by month
- **compare_metrics**: Compare revenue and costs

## Guardrails
- **currency**: "USD"
- **include_chart**: true

## Engine Preference
analytics
`;
  const parsed = parseSpecMarkdown(edited, original);
  assert.equal(parsed.objective, 'Compare monthly revenue and costs.');
  assert.deepEqual(parsed.data_requirements, ['data/finance.csv']);
  assert.deepEqual(
    parsed.capability_requirements.map(item => item.name),
    ['aggregate_monthly', 'compare_metrics'],
  );
  assert.deepEqual(parsed.constraints, { currency: 'USD', include_chart: true });
  assert.equal(parsed.engine_hint, 'analytics');
}

testSplitChunkParsing();
testMultipleEventsInOneChunk();
testMalformedRecordIsIgnored();
testUnknownEventIsIgnored();
testDeltaAndDoneTextReduction();
testPipelineProgressionAndCompletion();
testFailureMarksCurrentStage();
testEarlyFailureMarksFirstPendingStage();
testConfirmationPausesAtBuiltSpec();
testConfirmationEventParsesFromStream();
testBlankQueryUsesVisibleFallbackAndOmitsApiInput();
testEmptySourcesRejectSubmission();
testSpecMarkdownRoundTrip();
testEditedMarkdownUpdatesStructuredSpec();
console.log('responses SSE tests passed');
