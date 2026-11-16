// Minimalny router oparty o hash (#/studio) - serwer zawsze serwuje index.html.
import { useSyncExternalStore } from 'react';

export type RouteKey = 'dashboard' | 'studio' | 'agents' | 'export' | 'models' | 'developer' | 'settings';

export const ROUTES: RouteKey[] = ['dashboard', 'studio', 'agents', 'export', 'models', 'developer', 'settings'];

function parse(): RouteKey {
  const h = location.hash.replace(/^#\/?/, '').split(/[/?]/)[0];
  return (ROUTES as string[]).includes(h) ? (h as RouteKey) : 'dashboard';
}

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb);
  return () => window.removeEventListener('hashchange', cb);
}

export function useRoute(): RouteKey {
  return useSyncExternalStore(subscribe, parse, parse);
}

export function navigate(r: RouteKey) {
  if (parse() !== r || !location.hash) location.hash = `#/${r}`;
}
