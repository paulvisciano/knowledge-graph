export const SUPPORTED_IMAGE_TYPES = new Set([
  'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp', 'image/svg+xml',
]);

export const SUPPORTED_TEXT_TYPES = new Set([
  'text/plain', 'text/markdown', 'text/csv', 'application/json',
  'text/html', 'text/xml', 'application/xml', 'text/yaml', 'text/x-log',
]);

export const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20MB
export const MAX_ATTACHMENTS = 5;

export interface Attachment {
  id: string;
  file: File;
  mimeType: string;
  dataUrl: string;
  thumbnailUrl?: string;
  name: string;
  size: number;
}

export function isImageType(mimeType: string): boolean {
  return SUPPORTED_IMAGE_TYPES.has(mimeType);
}

export function isTextType(mimeType: string): boolean {
  return SUPPORTED_TEXT_TYPES.has(mimeType);
}

export async function fileToAttachment(file: File): Promise<Attachment> {
  if (file.size > MAX_FILE_SIZE) {
    throw new Error(`File "${file.name}" exceeds ${MAX_FILE_SIZE / 1024 / 1024}MB limit`);
  }

  const mimeType = file.type || guessMimeType(file.name);

  if (isImageType(mimeType)) {
    return fileToImageAttachment(file, mimeType);
  }

  if (isTextType(mimeType)) {
    return fileToTextAttachment(file, mimeType);
  }

  throw new Error(`Unsupported file type: ${mimeType || 'unknown'}. Supported: images (PNG, JPEG, GIF, WebP, BMP, SVG) and text files.`);
}

async function fileToImageAttachment(file: File, mimeType: string): Promise<Attachment> {
  const dataUrl = await readFileAsDataUrl(file);
  const thumbnailUrl = URL.createObjectURL(file);

  return {
    id: crypto.randomUUID(),
    file,
    mimeType,
    dataUrl,
    thumbnailUrl,
    name: file.name,
    size: file.size,
  };
}

async function fileToTextAttachment(file: File, mimeType: string): Promise<Attachment> {
  const text = await file.text();
  const dataUrl = `data:${mimeType};charset=utf-8,${encodeURIComponent(text)}`;

  return {
    id: crypto.randomUUID(),
    file,
    mimeType,
    dataUrl,
    name: file.name,
    size: file.size,
  };
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error(`Failed to read file: ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function guessMimeType(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase();
  const map: Record<string, string> = {
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
    gif: 'image/gif', webp: 'image/webp', bmp: 'image/bmp',
    svg: 'image/svg+xml', txt: 'text/plain', md: 'text/markdown',
    csv: 'text/csv', json: 'application/json', html: 'text/html',
    htm: 'text/html', xml: 'application/xml', yaml: 'text/yaml',
    yml: 'text/yaml', log: 'text/x-log',
  };
  return map[ext || ''] || 'application/octet-stream';
}

/**
 * OpenAI Vision API content format.
 * Text-only messages return a plain string.
 * Messages with attachments return an array of typed content parts.
 */
export function buildMessageContent(
  text: string,
  attachments: Attachment[]
): string | Array<{ type: string; text?: string; image_url?: { url: string } }> {
  const images = attachments.filter((a) => isImageType(a.mimeType));
  const texts = attachments.filter((a) => isTextType(a.mimeType));

  if (images.length === 0 && texts.length === 0) {
    return text;
  }

  const parts: Array<{ type: string; text?: string; image_url?: { url: string } }> = [];

  let fullText = text;
  for (const textAtt of texts) {
    const content = decodeURIComponent(textAtt.dataUrl.split(',').slice(1).join(','));
    fullText += `\n\n--- Attached: ${textAtt.name} ---\n${content}`;
  }

  if (fullText.trim()) {
    parts.push({ type: 'text', text: fullText });
  }

  for (const img of images) {
    parts.push({ type: 'image_url', image_url: { url: img.dataUrl } });
  }

  return parts;
}

export function revokeAttachmentUrls(attachments: Attachment[]): void {
  for (const att of attachments) {
    if (att.thumbnailUrl) {
      URL.revokeObjectURL(att.thumbnailUrl);
    }
  }
}