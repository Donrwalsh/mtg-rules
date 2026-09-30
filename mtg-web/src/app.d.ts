import type { ZoomedCard } from '$lib/overlays';

declare global {
  namespace App {
    // Shallow-routing state (see $lib/overlays): each open overlay is a
    // history entry, so the browser's back button closes it.
    interface PageState {
      sheet?: true;
      zoom?: ZoomedCard;
    }
  }
}

export {};
