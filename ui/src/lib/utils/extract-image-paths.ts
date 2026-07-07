export function extractImageFilePaths(text: string): string[] {
  const paths = new Set<string>();

  const refsMarker = '---IMAGE_REFS---';
  const refsIdx = text.indexOf(refsMarker);
  if (refsIdx !== -1) {
    const refsSection = text.slice(refsIdx + refsMarker.length);
    for (const line of refsSection.split('\n')) {
      const trimmed = line.trim();
      if (trimmed && trimmed !== 'unknown_source') {
        paths.add(trimmed);
      }
    }
  }

  return [...paths];
}