export type ChatRole = 'user' | 'assistant';
export type ChatMessageStatus = 'streaming' | 'awaiting_confirmation' | 'completed' | 'failed' | 'cancelled';
export type PipelineStageStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface CorpusRequest {
  sources: string[];
  schemas: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface CreateResponseRequest {
  input?: string;
  data_corpus_package: CorpusRequest;
  session_id: string;
}

export interface PipelineStage {
  id: string;
  label: string;
  status: PipelineStageStatus;
}

export interface CapabilityRequirement {
  name: string;
  description?: string | null;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  constraints: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface EditableExecutionSpec {
  intent: string;
  objective: string;
  data_requirements: string[];
  capability_requirements: CapabilityRequirement[];
  constraints: Record<string, unknown>;
  confirmed: boolean;
  engine_hint?: string | null;
}

export interface ResponseConfirmationState {
  responseId: string;
  token: string;
  revision: number;
  intent: string;
  spec: EditableExecutionSpec;
  expiresAt: string;
  submitting?: boolean;
  error?: string;
}

export interface ResponsesChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  status: ChatMessageStatus;
  responseId?: string;
  stages?: PipelineStage[];
  evidence?: unknown;
  metadata?: Record<string, unknown>;
  error?: string;
  confirmation?: ResponseConfirmationState;
}

export interface ResponseCreatedEvent {
  type: 'response.created';
  response_id: string;
  response: { id: string; status: string };
}

export interface PipelineEvent {
  type: `pipeline.${string}`;
  response_id: string;
  [key: string]: unknown;
}

export interface OutputDeltaEvent {
  type: 'response.output_text.delta';
  response_id: string;
  delta: string;
}

export interface OutputDoneEvent {
  type: 'response.output_text.done';
  response_id: string;
  text: string;
}

export interface ResponseCompletedEvent {
  type: 'response.completed';
  response_id: string;
  response: { id: string; status: string; output_text: string };
  evidence?: unknown;
  metadata?: Record<string, unknown>;
}

export interface ResponseFailedEvent {
  type: 'response.failed';
  response_id: string;
  response: { id: string; status: string };
  error: { code: string; message: string };
}

export interface ResponseRequiresConfirmationEvent {
  type: 'response.requires_confirmation';
  response_id: string;
  revision: number;
  confirmation_token: string;
  intent: { value: string };
  spec: EditableExecutionSpec;
  expires_at: string;
}

export interface ResponseDecisionRequest {
  action: 'confirm' | 'revise';
  revision: number;
  feedback?: string;
  edited_spec?: Omit<EditableExecutionSpec, 'intent' | 'confirmed'>;
}

export interface ResponseHistorySummary {
  response_id: string;
  title: string;
  status: string;
  output_preview?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
}

export interface ResponseHistoryDetail {
  response_id: string;
  status: string;
  input: string;
  spec: EditableExecutionSpec;
  output_text?: string | null;
  evidence?: unknown;
  metadata: Record<string, unknown>;
  error?: { code: string; message: string } | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
}

export type ResponsesEvent =
  | ResponseCreatedEvent
  | PipelineEvent
  | OutputDeltaEvent
  | OutputDoneEvent
  | ResponseCompletedEvent
  | ResponseRequiresConfirmationEvent
  | ResponseFailedEvent;
