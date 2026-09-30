import { pushState } from '$app/navigation';
import { page } from '$app/state';

// The source sheet and card zoom are shallow-routing history entries, so
// the browser's back button closes them in the order they opened. Closing
// from the UI goes back too, which keeps one exit path. page.state starts
// empty on a fresh load, so a reload never reopens either.

export interface ZoomedCard {
  name: string;
  normal: string;
  large: string | null;
}

export function openZoom(card: ZoomedCard): void {
  // Keep `sheet`, so back from a zoom returns to the open sheet.
  pushState('', { ...page.state, zoom: card });
}

export function closeZoom(): void {
  if (page.state.zoom) history.back();
}

export function openSheet(): void {
  pushState('', { sheet: true });
}

export function closeSheet(): void {
  if (page.state.sheet) history.back();
}
