import Framework7 from 'framework7/lite-bundle';
import Framework7Svelte from 'framework7-svelte';

if (typeof window !== 'undefined') {
  Framework7.use(Framework7Svelte);
}

export { Framework7 };