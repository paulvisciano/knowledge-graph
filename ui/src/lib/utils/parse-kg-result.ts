const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif', '.svg'];

function isImageFilename(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext)) ||
    /\.raw-01/i.test(name) ||
    /\.ts-000-01/i.test(name)
  );
}

function stripPhotoSuffix(name: string): string {
  return name.replace(/\s*\(Photo\)\s*$/, '').trim();
}

interface KGEntity {
  entity: string;
  type?: string;
  description?: string;
}

interface KGRelationship {
  entity1: string;
  entity2: string;
  description: string;
}

export interface KGParsedResult {
  entities: KGEntity[];
  relationships: KGRelationship[];
  imagePaths: string[];
  contextText: string;
}

function extractJsonLines(section: string): string[] {
  const lines: string[] = [];
  const jsonBlockMatch = section.match(/```json\s*([\s\S]*?)```/);
  const jsonText = jsonBlockMatch ? jsonBlockMatch[1] : section;
  for (const line of jsonText.split('\n')) {
    const trimmed = line.trim();
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      lines.push(trimmed);
    }
  }
  return lines;
}

export function parseKGResult(text: string): KGParsedResult {
  const refsMarker = '---IMAGE_REFS---';
  const refsIdx = text.indexOf(refsMarker);
  const contextText = refsIdx !== -1 ? text.slice(0, refsIdx).trimEnd() : text;

  const entityIdx = contextText.indexOf('Knowledge Graph Data (Entity)');
  const relIdx = contextText.indexOf('Knowledge Graph Data (Relationship)');

  const entitySection = entityIdx !== -1
    ? contextText.slice(entityIdx, relIdx !== -1 ? relIdx : contextText.length)
    : '';
  const relSection = relIdx !== -1
    ? contextText.slice(relIdx)
    : '';

  const entities: KGEntity[] = [];
  for (const line of extractJsonLines(entitySection)) {
    try {
      const parsed = JSON.parse(line) as KGEntity;
      if (parsed.entity) entities.push(parsed);
    } catch { /* skip malformed */ }
  }

  const relationships: KGRelationship[] = [];
  for (const line of extractJsonLines(relSection)) {
    try {
      const parsed = JSON.parse(line) as KGRelationship;
      if (parsed.entity1 && parsed.entity2) relationships.push(parsed);
    } catch { /* skip malformed */ }
  }

  const imagePaths = new Set<string>();

  for (const e of entities) {
    const name = stripPhotoSuffix(e.entity);
    if (isImageFilename(name)) imagePaths.add(name);
  }

  for (const r of relationships) {
    const n1 = stripPhotoSuffix(r.entity1);
    const n2 = stripPhotoSuffix(r.entity2);
    if (isImageFilename(n1)) imagePaths.add(n1);
    if (isImageFilename(n2)) imagePaths.add(n2);
  }

  if (refsIdx !== -1) {
    const refsSection = text.slice(refsIdx + refsMarker.length);
    for (const line of refsSection.split('\n')) {
      const trimmed = line.trim();
      if (trimmed && trimmed !== 'unknown_source') imagePaths.add(trimmed);
    }
  }

  const imageTokenPattern = /\b([A-Za-z0-9_\-]+\.(?:RAW-01|TS-000-01)(?:\.[A-Za-z0-9]+)?)\b/g;
  for (const m of contextText.matchAll(imageTokenPattern)) {
    if (isImageFilename(m[1])) imagePaths.add(m[1]);
  }
  const standardImagePattern = /\b([A-Za-z0-9_\-]+\.(?:png|jpg|jpeg|gif|webp|bmp|tiff|tif|svg))\b/gi;
  for (const m of contextText.matchAll(standardImagePattern)) {
    if (isImageFilename(m[1]) && !m[1].match(/^(?:RAW-01|TS-000-01)\./i)) {
      imagePaths.add(m[1]);
    }
  }

  return { entities, relationships, imagePaths: [...imagePaths], contextText };
}