import type { ResponsesChatMessage, ResponsesEvent } from '@/types/responses';
import { prepareResponseSubmission } from '@/utils/responses-request';
import {
  ResponsesSSEParser,
  applyResponseEvent,
  createPipelineStages,
  markResponseFailure,
} from '@/utils/responses-sse';
import { useCallback, useRef, useState } from 'react';

function createId(prefix: string): string {
  const value = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${value}`;
}

async function readHttpError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    return payload?.detail || payload?.message || `Request failed with status ${response.status}`;
  } catch {
    return `Request failed with status ${response.status}`;
  }
}

export function useResponsesChat() {
  const [messages, setMessages] = useState<ResponsesChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef(createId('session'));

  const updateAssistant = useCallback(
    (id: string, updater: (message: ResponsesChatMessage) => ResponsesChatMessage) => {
      setMessages(current => current.map(message => (message.id === id ? updater(message) : message)));
    },
    [],
  );

  const submit = useCallback(
    async (input: string, sources: string[]) => {
      if (controllerRef.current) return false;

      const submission = prepareResponseSubmission(input, sources, sessionIdRef.current);
      if (!submission) {
        setValidationError('Enter a question or add a server-side data source.');
        return false;
      }

      setValidationError(null);
      const userMessage: ResponsesChatMessage = {
        id: createId('user'),
        role: 'user',
        content: submission.visibleInput,
        status: 'completed',
      };
      const assistantId = createId('assistant');
      const assistantMessage: ResponsesChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        status: 'streaming',
        stages: createPipelineStages(),
      };
      setMessages(current => [...current, userMessage, assistantMessage]);
      setIsStreaming(true);

      const controller = new AbortController();
      controllerRef.current = controller;
      let terminalEventReceived = false;

      try {
        const response = await fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/responses`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(submission.request),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(await readHttpError(response));
        if (!response.body) throw new Error('The response stream is unavailable.');

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        const parser = new ResponsesSSEParser();
        const applyEvents = (events: ResponsesEvent[]) => {
          for (const event of events) {
            if (event.type === 'response.completed' || event.type === 'response.failed') {
              terminalEventReceived = true;
            }
            updateAssistant(assistantId, message => applyResponseEvent(message, event));
          }
        };

        let streamDone = false;
        while (!streamDone) {
          const { done, value } = await reader.read();
          streamDone = done;
          if (value) applyEvents(parser.push(decoder.decode(value, { stream: true })));
        }
        applyEvents(parser.push(decoder.decode()));
        applyEvents(parser.finish());
        if (!terminalEventReceived) {
          updateAssistant(assistantId, message =>
            markResponseFailure(message, 'The response stream ended before completion.'),
          );
        }
      } catch (error) {
        if (controller.signal.aborted) {
          updateAssistant(assistantId, message => ({
            ...message,
            status: 'cancelled',
            error: undefined,
          }));
        } else {
          const errorMessage = error instanceof Error ? error.message : 'Unable to complete the request.';
          updateAssistant(assistantId, message => markResponseFailure(message, errorMessage));
        }
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
        setIsStreaming(false);
      }
      return true;
    },
    [updateAssistant],
  );

  const stop = useCallback(() => controllerRef.current?.abort(), []);
  const clear = useCallback(() => {
    controllerRef.current?.abort();
    setMessages([]);
    setValidationError(null);
  }, []);

  return { messages, isStreaming, validationError, submit, stop, clear };
}
