import type { EditableExecutionSpec, PipelineStage, ResponsesChatMessage, ResponsesEvent } from '@/types/responses';
import { specToMarkdown } from '@/utils/spec-markdown';

const PIPELINE_STAGE_DEFINITIONS = [
  ['pipeline.start', 'Starting workflow'],
  ['pipeline.intent_analyzed', 'Understanding intent'],
  ['pipeline.spec_built', 'Planning execution'],
  ['pipeline.spec_revised', 'Revising plan'],
  ['pipeline.spec_confirmed', 'Confirming plan'],
  ['pipeline.engine_selected', 'Selecting engine'],
  ['pipeline.engine_completed', 'Running analysis'],
  ['pipeline.evidence_collected', 'Collecting evidence'],
  ['pipeline.completed', 'Finalizing response'],
] as const;

export interface RuntimeEventOutputContent {
  eventType: string;
  phase: string;
  status: string;
  name: string;
  summary: string;
  description?: string;
  details: Record<string, unknown>;
  artifactRefs: string[];
  code?: {
    name: string;
    language: string;
    content: string;
    truncated: boolean;
    artifactRef?: string | null;
  };
}

export type PipelineStageOutput =
  | { output_type: 'markdown'; content: string }
  | { output_type: 'event'; content: RuntimeEventOutputContent };

export interface PipelineExecutionStep {
  id: string;
  title: string;
  detail: string;
  status: 'running' | 'done' | 'failed';
}

function humanizeEventName(value: unknown): string {
  const normalized = String(value || '')
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : 'Execution event';
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function runtimeEventSummary(event: Record<string, unknown>): string {
  const description = typeof event.description === 'string' ? event.description.trim() : '';
  if (description) return description;

  const name = String(event.name || event.event_type || 'step');
  const details = asRecord(event.details);
  const outputs = asRecord(details.outputs);
  const validation = asRecord(outputs.validation);
  const validationStatus = validation.status;
  if (validationStatus) return `Validation ${String(validationStatus).toLowerCase()}.`;

  if (name === 'datascience_agent') {
    const inputs = asRecord(details.inputs);
    const profile = asRecord(inputs.profile);
    const rowCount = Number(profile.row_count || 0);
    const metricCount = Number(outputs.metric_count || 0);
    const chartCount = Number(outputs.chart_dataset_count || 0);
    const parts = [
      rowCount ? `${rowCount.toLocaleString()} rows profiled` : '',
      metricCount ? `${metricCount} metrics` : '',
      chartCount ? `${chartCount} chart dataset${chartCount === 1 ? '' : 's'}` : '',
    ].filter(Boolean);
    if (parts.length) return parts.join(' · ');
  }

  if (name === 'renderer') {
    const formats = Array.isArray(outputs.rendered_formats) ? outputs.rendered_formats.map(String) : [];
    if (formats.length) return `Created ${formats.length} report formats: ${formats.join(', ')}.`;
  }

  if (name === 'chart_agent' && outputs.selected_type) {
    return `Prepared a ${humanizeEventName(outputs.selected_type).toLowerCase()} chart.`;
  }

  if (name === 'report_agent' && outputs.report_format) {
    return `Built the ${humanizeEventName(outputs.report_format).toLowerCase()}.`;
  }

  const status = String(event.status || 'completed').toLowerCase();
  return `${humanizeEventName(name)} ${status}.`;
}

export function getPipelineStageLabel(eventType: string): string {
  return PIPELINE_STAGE_DEFINITIONS.find(([id]) => id === eventType)?.[1] || eventType;
}

export function getPipelineExecutionStep(event: Record<string, unknown>): PipelineExecutionStep {
  const eventType = String(event.type || 'pipeline.unknown');
  if (eventType === 'pipeline.runtime_event') {
    const runtimeStatus = String(event.status || 'completed');
    const status = ['failed', 'cancelled'].includes(runtimeStatus)
      ? 'failed'
      : ['pending', 'running'].includes(runtimeStatus)
        ? 'running'
        : 'done';
    const runtimeEventType = humanizeEventName(event.event_type);
    const phase = humanizeEventName(event.phase);
    return {
      id: `${eventType}:${String(event.event_id || event.sequence || event.name || event.event_type || 'event')}`,
      title: humanizeEventName(event.name || event.event_type),
      detail: runtimeEventSummary(event) || `${runtimeEventType} · ${phase}`,
      status,
    };
  }
  return {
    id: eventType,
    title: getPipelineStageLabel(eventType),
    detail: '',
    status: 'running',
  };
}

function intentValue(event: Record<string, unknown>, spec?: EditableExecutionSpec): string {
  const intent = event.intent;
  if (typeof intent === 'string' && intent.trim()) return intent.trim();
  if (intent && typeof intent === 'object') {
    const value = (intent as Record<string, unknown>).value;
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return spec?.intent || 'unknown';
}

function preliminarySpecMarkdown(event: Record<string, unknown>): string {
  const objective = typeof event.objective === 'string' && event.objective.trim() ? event.objective.trim() : 'Pending';
  const intent = intentValue(event);
  const dataCount = Number(event.data_requirement_count || 0);
  const capabilityCount = Number(event.capability_count || 0);

  return `# Analysis Plan

## Objective
${objective}

## Intent
${intent}

## Data
${dataCount} data requirement${dataCount === 1 ? '' : 's'} selected

## Workflow
${capabilityCount} capability requirement${capabilityCount === 1 ? '' : 's'} selected

## Guardrails
Full details will be available in the proposed plan.

## Engine Preference
Automatic
`;
}

export function getPipelineStageOutput(
  event: Record<string, unknown>,
  spec?: EditableExecutionSpec,
): PipelineStageOutput | null {
  if (event.type === 'pipeline.runtime_event') {
    const artifactRefs = Array.isArray(event.artifact_refs) ? event.artifact_refs.map(String) : [];
    const rawCode = asRecord(event.code);
    const code =
      typeof rawCode.content === 'string' && rawCode.content.trim()
        ? {
            name: String(rawCode.name || 'generated-code.py'),
            language: String(rawCode.language || 'python'),
            content: rawCode.content,
            truncated: Boolean(rawCode.truncated),
            artifactRef: rawCode.artifact_ref ? String(rawCode.artifact_ref) : null,
          }
        : undefined;
    return {
      output_type: 'event',
      content: {
        eventType: String(event.event_type || 'runtime.event'),
        phase: String(event.phase || 'engine'),
        status: String(event.status || 'completed'),
        name: String(event.name || event.event_type || 'Execution event'),
        summary: runtimeEventSummary(event),
        ...(typeof event.description === 'string' && event.description.trim()
          ? { description: event.description.trim() }
          : {}),
        details: asRecord(event.details),
        artifactRefs,
        ...(code ? { code } : {}),
      },
    };
  }
  if (event.type === 'pipeline.start' && event.artifact_ref) {
    return {
      output_type: 'markdown',
      content: `**Artifact bundle:** \`${String(event.artifact_ref)}\``,
    };
  }
  if (event.type === 'pipeline.intent_analyzed') {
    return {
      output_type: 'markdown',
      content: `# Intent\n\n**Classification:** \`${intentValue(event, spec)}\``,
    };
  }
  if (event.type === 'pipeline.spec_built' || event.type === 'pipeline.spec_revised') {
    return {
      output_type: 'markdown',
      content: spec ? specToMarkdown(spec) : preliminarySpecMarkdown(event),
    };
  }
  if (event.type === 'pipeline.spec_confirmed') {
    const status = event.confirmed === false ? 'Pending confirmation' : 'Confirmed';
    const content = spec
      ? `> **Status:** ${status}\n\n${specToMarkdown(spec)}`
      : `# Plan Confirmation\n\n**Status:** ${status}\n\n**Engine:** ${String(event.engine_hint || 'Automatic')}`;
    return { output_type: 'markdown', content };
  }
  return null;
}

const RESPONSE_EVENT_TYPES = new Set([
  'response.created',
  'response.output_text.delta',
  'response.output_text.done',
  'response.completed',
  'response.requires_confirmation',
  'response.failed',
  'pipeline.runtime_event',
  ...PIPELINE_STAGE_DEFINITIONS.map(([id]) => id),
]);

export function createPipelineStages(): PipelineStage[] {
  return PIPELINE_STAGE_DEFINITIONS.map(([id, label]) => ({ id, label, status: 'pending' }));
}

function advanceStages(stages: PipelineStage[], eventType: string): PipelineStage[] {
  const activeIndex = stages.findIndex(stage => stage.id === eventType);
  if (activeIndex < 0) return stages;
  return stages.map((stage, index) => ({
    ...stage,
    status: index < activeIndex ? 'completed' : index === activeIndex ? 'running' : 'pending',
  }));
}

export function markResponseFailure(
  message: ResponsesChatMessage,
  error: string,
  responseId?: string,
): ResponsesChatMessage {
  const currentStages = message.stages || createPipelineStages();
  const runningIndex = currentStages.findIndex(stage => stage.status === 'running');
  const failedIndex = runningIndex >= 0 ? runningIndex : currentStages.findIndex(stage => stage.status === 'pending');
  const stages = currentStages.map((stage, index) =>
    index === failedIndex ? { ...stage, status: 'failed' as const } : stage,
  );
  return {
    ...message,
    status: 'failed',
    responseId: responseId || message.responseId,
    stages,
    error,
  };
}

export function applyResponseEvent(message: ResponsesChatMessage, event: ResponsesEvent): ResponsesChatMessage {
  if (event.type === 'response.created') {
    return { ...message, responseId: event.response_id };
  }
  if (event.type.startsWith('pipeline.')) {
    return {
      ...message,
      stages: advanceStages(message.stages || createPipelineStages(), event.type),
    };
  }
  if (event.type === 'response.output_text.delta') {
    return { ...message, content: message.content + event.delta };
  }
  if (event.type === 'response.output_text.done') {
    return { ...message, content: event.text };
  }
  if (event.type === 'response.completed') {
    return {
      ...message,
      content: event.response.output_text,
      status: 'completed',
      responseId: event.response_id,
      stages: (message.stages || createPipelineStages()).map(stage => ({
        ...stage,
        status: 'completed',
      })),
      evidence: event.evidence,
      metadata: event.metadata,
    };
  }
  if (event.type === 'response.requires_confirmation') {
    const stages = (message.stages || createPipelineStages()).map((stage, index) => ({
      ...stage,
      status: index <= 2 ? ('completed' as const) : ('pending' as const),
    }));
    return {
      ...message,
      status: 'awaiting_confirmation',
      responseId: event.response_id,
      stages,
      confirmation: {
        responseId: event.response_id,
        token: event.confirmation_token,
        revision: event.revision,
        intent: event.intent.value,
        spec: event.spec,
        expiresAt: event.expires_at,
      },
    };
  }
  if (event.type === 'response.failed') {
    return markResponseFailure(message, event.error.message, event.response_id);
  }
  return message;
}

export class ResponsesSSEParser {
  private buffer = '';

  push(chunk: string): ResponsesEvent[] {
    this.buffer += chunk.replace(/\r\n/g, '\n');
    const records = this.buffer.split('\n\n');
    this.buffer = records.pop() || '';
    return records.flatMap(record => this.parseRecord(record));
  }

  finish(): ResponsesEvent[] {
    const record = this.buffer.trim();
    this.buffer = '';
    return record ? this.parseRecord(record) : [];
  }

  private parseRecord(record: string): ResponsesEvent[] {
    let eventName = '';
    const dataLines: string[] = [];
    for (const line of record.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (!RESPONSE_EVENT_TYPES.has(eventName) || dataLines.length === 0) return [];
    try {
      const payload = JSON.parse(dataLines.join('\n')) as ResponsesEvent;
      return payload && payload.type === eventName ? [payload] : [];
    } catch {
      return [];
    }
  }
}
