import type { CreateResponseRequest } from '@/types/responses';

export const DEFAULT_RESPONSE_QUERY = 'Analyze this data corpus.';

export type PreparedResponseSubmission = {
  visibleInput: string;
  request: CreateResponseRequest;
};

export function prepareResponseSubmission(
  input: string,
  sources: string[],
  sessionId: string,
): PreparedResponseSubmission | null {
  const normalizedSources = sources.map(source => source.trim()).filter(Boolean);
  const normalizedInput = input.trim();
  if (!normalizedInput && normalizedSources.length === 0) return null;

  return {
    visibleInput: normalizedInput || DEFAULT_RESPONSE_QUERY,
    request: {
      ...(normalizedInput ? { input: normalizedInput } : {}),
      data_corpus_package: { sources: normalizedSources, schemas: {}, metadata: {} },
      session_id: sessionId,
    },
  };
}
