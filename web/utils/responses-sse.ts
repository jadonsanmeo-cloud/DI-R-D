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

export interface PipelineStageOutput {
  output_type: 'markdown';
  content: string;
}

export function getPipelineStageLabel(eventType: string): string {
  return PIPELINE_STAGE_DEFINITIONS.find(([id]) => id === eventType)?.[1] || eventType;
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
