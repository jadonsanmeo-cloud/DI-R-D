import type { CapabilityRequirement, EditableExecutionSpec } from '@/types/responses';

function displayValue(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

function uxField(markdown: string, field: string): string {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const heading = `**${field.toLowerCase()}:**`;
  const start = lines.findIndex(line => line.trim().toLowerCase().startsWith(heading));
  if (start < 0) return '';
  const firstLine = lines[start].trim().slice(heading.length).trim();
  const content = firstLine ? [firstLine] : [];
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^\*\*[^*]+:\*\*/.test(lines[index].trim())) break;
    content.push(lines[index]);
  }
  return content.join('\n').trim();
}

function agentField(markdown: string, field: string): string {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const heading = `**${field.toLowerCase()}**`;
  const start = lines.findIndex(line => line.trim().toLowerCase() === heading);
  if (start < 0) return '';
  const content: string[] = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^\*\*[a-z_]+\*\*$/i.test(lines[index].trim())) break;
    content.push(lines[index]);
  }
  return content.join('\n').trim();
}

function parseJsonField(value: string, field: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    throw new Error(`${field} must contain valid JSON.`);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
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

function capabilityFromValues(
  name: string,
  description: string | null,
  original: EditableExecutionSpec,
): CapabilityRequirement {
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

function parseCapabilityRequirements(value: string, original: EditableExecutionSpec): CapabilityRequirement[] {
  const capabilities: CapabilityRequirement[] = [];
  let name = '';
  let description: string | null = null;

  const flush = () => {
    if (!name) return;
    capabilities.push(capabilityFromValues(name, description, original));
    name = '';
    description = null;
  };

  for (const line of value.split('\n')) {
    const match = line.match(/^\s*-\s*(name|description)\s*:\s*(.*?)\s*$/i);
    if (!match) continue;
    if (match[1].toLowerCase() === 'name') {
      flush();
      name = match[2].trim();
    } else if (name) {
      description = match[2].trim() || null;
    }
  }
  flush();
  return capabilities;
}

export function specToMarkdown(spec: EditableExecutionSpec): string {
  const fields = [
    spec.objective.trim() ? `**Objective:** ${spec.objective.trim()}` : '',
    spec.capability_requirements.length
      ? `**Capability requirements:**\n${spec.capability_requirements
          .flatMap(item => [
            `- name: ${item.name}`,
            ...(item.description ? [`- description: ${item.description.replace(/\s+/g, ' ').trim()}`] : []),
          ])
          .join('\n')}`
      : '',
    Object.keys(spec.constraints).length
      ? `**Constraints:**\n${Object.entries(spec.constraints)
          .map(([key, value]) => `- ${key}: ${displayValue(value)}`)
          .join('\n')}`
      : '',
    spec.data_requirements.length
      ? `**Data requirements:**\n${spec.data_requirements.map(item => `- ${item}`).join('\n')}`
      : '',
    spec.engine_hint ? `**Engine preference:** ${spec.engine_hint}` : '',
  ].filter(Boolean);

  return `${fields.join('\n\n')}\n`;
}

export function parseSpecMarkdown(markdown: string, original: EditableExecutionSpec): EditableExecutionSpec {
  const uxObjective = uxField(markdown, 'Objective');
  if (uxObjective) {
    const capabilityText = uxField(markdown, 'Capability requirements');
    const constraints: Record<string, unknown> = {};
    for (const line of bulletLines(uxField(markdown, 'Constraints'))) {
      const match = line.match(/^([^:]+)\s*:\s*(.+)$/);
      if (match) constraints[match[1].trim()] = parseValue(match[2]);
    }
    const engineText = uxField(markdown, 'Engine preference');

    return {
      ...original,
      objective: uxObjective,
      capability_requirements: capabilityText ? parseCapabilityRequirements(capabilityText, original) : [],
      constraints,
      data_requirements: bulletLines(uxField(markdown, 'Data requirements')),
      engine_hint: engineText || null,
      confirmed: false,
    };
  }

  if (agentField(markdown, 'objective')) {
    const objective = agentField(markdown, 'objective');
    const capabilityText = agentField(markdown, 'capability_requirements');
    const constraintsText = agentField(markdown, 'constraints');
    const dataText = agentField(markdown, 'data_requirements');
    const engineText = agentField(markdown, 'engine_hint');

    const capabilityRequirements = capabilityText
      ? parseJsonField(capabilityText, 'capability_requirements')
      : [];
    if (
      !Array.isArray(capabilityRequirements) ||
      capabilityRequirements.some(item => !isObject(item) || typeof item.name !== 'string')
    ) {
      throw new Error('capability_requirements must be a JSON array of capability objects.');
    }

    const constraints = constraintsText ? parseJsonField(constraintsText, 'constraints') : {};
    if (!isObject(constraints)) throw new Error('constraints must be a JSON object.');

    const dataRequirements = dataText ? parseJsonField(dataText, 'data_requirements') : [];
    if (!Array.isArray(dataRequirements) || dataRequirements.some(item => typeof item !== 'string')) {
      throw new Error('data_requirements must be a JSON array of strings.');
    }

    const engineHint = engineText ? parseValue(engineText) : null;
    if (engineHint !== null && typeof engineHint !== 'string') {
      throw new Error('engine_hint must be a string or null.');
    }

    return {
      ...original,
      objective,
      capability_requirements: capabilityRequirements as CapabilityRequirement[],
      constraints,
      data_requirements: dataRequirements,
      engine_hint: engineHint,
      confirmed: false,
    };
  }

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
