const IMAGE_EXTENSIONS = [
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif', '.svg',
];

const IMAGE_TOKEN_PATTERN = /\b([A-Za-z0-9_\-]+\.(?:RAW-01|TS-000-01)(?:\.[A-Za-z0-9]+)?)\b/g;
const STANDARD_IMAGE_PATTERN = /\b([A-Za-z0-9_\-]+\.(?:png|jpg|jpeg|gif|webp|bmp|tiff|tif|svg))\b/gi;

function looksLikeImagePath(path: string): boolean {
  const lower = path.toLowerCase();
  return (
    IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext)) ||
    /\.raw-01$/i.test(path) ||
    /\.ts-000-01$/i.test(path)
  );
}

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

  for (const match of text.matchAll(IMAGE_TOKEN_PATTERN)) {
    paths.add(match[1]);
  }
  for (const match of text.matchAll(STANDARD_IMAGE_PATTERN)) {
    paths.add(match[1]);
  }

  return [...paths].filter(looksLikeImagePath);
}