import type { CapabilityRequirement, EditableExecutionSpec } from '@/types/responses';

function displayValue(value: unknown): string {
  if (typeof value === 'string') return JSON.stringify(value);
  return JSON.stringify(value);
}

function section(markdown: string, heading: string): string {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const start = lines.findIndex(line => line.trim().toLowerCase() === `## ${heading.toLowerCase()}`);
  if (start < 0) return '';
  const content: string[] = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    if (lines[index].trim().startsWith('## ')) break;
    content.push(lines[index]);
  }
  return content.join('\n').trim();
}

function bulletLines(value: string): string[] {
  return value
    .split('\n')
    .map(line => line.match(/^\s*[-*]\s+(.+?)\s*$/)?.[1])
    .filter((line): line is string => Boolean(line));
}

function parseValue(value: string): unknown {
  const normalized = value.trim().replace(/^`|`$/g, '');
  try {
    return JSON.parse(normalized);
  } catch {
    return normalized;
  }
}

function capabilityFromLine(line: string, original: EditableExecutionSpec): CapabilityRequirement {
  const match = line.match(/^\*\*([^*]+)\*\*(?::|\s+[—-])?\s*(.*)$/);
  const name = (match?.[1] || line).trim();
  const description = match?.[2]?.trim() || null;
  const existing = original.capability_requirements.find(item => item.name === name);
  return {
    name,
    description,
    input_schema: existing?.input_schema || {},
    output_schema: existing?.output_schema || {},
    constraints: existing?.constraints || {},
    metadata: existing?.metadata || {},
  };
}

export function specToMarkdown(spec: EditableExecutionSpec): string {
  const data = spec.data_requirements.length
    ? spec.data_requirements.map(item => `- ${item}`).join('\n')
    : '- No additional data requirements';
  const workflow = spec.capability_requirements.length
    ? spec.capability_requirements
        .map(item => `- **${item.name}**${item.description ? `: ${item.description}` : ''}`)
        .join('\n')
    : '- Use the available data tools needed to complete the objective';
  const guardrails = Object.keys(spec.constraints).length
    ? Object.entries(spec.constraints)
        .map(([key, value]) => `- **${key}**: ${displayValue(value)}`)
        .join('\n')
    : '- No additional guardrails';

  return `# Analysis Plan

## Objective
${spec.objective}

## Data
${data}

## Workflow
${workflow}

## Guardrails
${guardrails}

## Engine Preference
${spec.engine_hint || 'Automatic'}
`;
}

export function parseSpecMarkdown(markdown: string, original: EditableExecutionSpec): EditableExecutionSpec {
  const objective = section(markdown, 'Objective');
  if (!objective) throw new Error('The Objective section cannot be empty.');

  const dataLines = bulletLines(section(markdown, 'Data')).filter(
    line => line.toLowerCase() !== 'no additional data requirements',
  );
  const workflowLines = bulletLines(section(markdown, 'Workflow')).filter(
    line => !line.toLowerCase().startsWith('use the available data tools'),
  );
  const constraints: Record<string, unknown> = {};
  for (const line of bulletLines(section(markdown, 'Guardrails'))) {
    if (line.toLowerCase() === 'no additional guardrails') continue;
    const match = line.match(/^(?:\*\*|`)?([^*`:]+)(?:\*\*|`)?\s*:\s*(.+)$/);
    if (match) constraints[match[1].trim()] = parseValue(match[2]);
  }
  const engineText = section(markdown, 'Engine Preference').trim();

  return {
    ...original,
    objective,
    data_requirements: dataLines,
    capability_requirements: workflowLines.map(line => capabilityFromLine(line, original)),
    constraints,
    engine_hint: !engineText || engineText.toLowerCase() === 'automatic' ? null : engineText,
    confirmed: false,
  };
}
