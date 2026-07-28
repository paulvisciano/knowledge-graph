import { registerProvider } from '../NodeKindProvider';
import { conversationProvider } from './conversation';
import { noteProvider } from './note';
import { docChunkProvider } from './docChunk';
import { photoProvider } from './photo';
import { personProvider } from './person';
import { locationProvider } from './location';
import { eventProvider } from './event';

// Order matters: note/docChunk before photo (the note-fix invariant —
// a spurious (Photo) hub with a note/chunk source_id must reclassify
// before the photo check matches on its (Photo) label/entity_type).
// Conversation first for safety (it would never match isPhotoNode anyway).
registerProvider(conversationProvider);
registerProvider(noteProvider);
registerProvider(docChunkProvider);
registerProvider(photoProvider);
registerProvider(personProvider);
registerProvider(locationProvider);
registerProvider(eventProvider);

// concept is the fallback (no registration) — getProvider('concept')
// returns undefined; NodePlane uses a default planeConfig.
export { classifyKind, getProvider, registerProvider } from '../NodeKindProvider';