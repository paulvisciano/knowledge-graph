/**
 * Parse image filenames from a Markdown `### References` section in an assistant
 * response (e.g. LightRAG kg-direct output).
 *
 * Matches entries like:
 *   - [1] PXL_20260626_231354860.RAW-01.jpg
 *   * [2] IMG_1234.HEIC
 *   [3] vacation.png
 *
 * Returns basenames (no directory) for files whose extension looks like a
 * raster/photo image. Non-image documents are ignored.
 */

const IMAGE_EXT = /\.(jpe?g|png|gif|webp|bmp|tiff?|heic|heif|raw|cr2|nef|arw|dng|orf|rw2|psd)$/i;

export function extractReferenceImages(content: string): string[] {
  if (!content) return [];

  // Find the last `### References` heading (case-insensitive) and take everything after.
  const refsIdx = content.search(/###\s*references\b/i);
  if (refsIdx === -1) return [];

  const refsSection = content.slice(refsIdx);
  const seen = new Set<string>();

  // Match optional bullet/dash/asterisk, optional `[n]`, then a filename token.
  // Filenames may contain letters, digits, dots, underscores, hyphens, spaces.
  const lineRe = /^\s*[-*]?\s*(?:\[\d+\]\s*)?([\w.\- ]+?\.(?:jpe?g|png|gif|webp|bmp|tiff?|heic|heif|raw|cr2|nef|arw|dng|orf|rw2|psd))\s*$/gim;

  let match: RegExpExecArray | null;
  while ((match = lineRe.exec(refsSection)) !== null) {
    const name = match[1].trim();
    if (!name || !IMAGE_EXT.test(name)) continue;
    seen.add(name);
  }

  return [...seen];
}