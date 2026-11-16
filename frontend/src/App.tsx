import { AnimatePresence, motion } from 'framer-motion';
import { ErrorBoundary, OfflineBanner, Toaster } from './components/common';
import { Sidebar, Topbar } from './components/layout';
import { ConfirmHost } from './components/ui';
import { useRoute, type RouteKey } from './lib/router';
import AgentsPage from './pages/Agents';
import DashboardPage from './pages/Dashboard';
import DeveloperPage from './pages/Developer';
import ExportPage from './pages/Export';
import ModelsPage from './pages/Models';
import SettingsPage from './pages/Settings';
import StudioPage from './pages/Studio';

const PAGES: Record<RouteKey, () => React.ReactElement> = {
  dashboard: DashboardPage,
  studio: StudioPage,
  agents: AgentsPage,
  export: ExportPage,
  models: ModelsPage,
  developer: DeveloperPage,
  settings: SettingsPage,
};

export default function App() {
  const route = useRoute();
  const Page = PAGES[route];
  const fullBleed = route === 'studio';
  return (
    <div className="bg-app flex h-full w-full overflow-hidden text-text">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <OfflineBanner />
        <main className="relative min-h-0 flex-1">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={route}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
              className={fullBleed ? 'absolute inset-0' : 'scroll-thin absolute inset-0 overflow-y-auto'}
            >
              <ErrorBoundary name={route}>
                {fullBleed ? (
                  <Page />
                ) : (
                  <div className="mx-auto w-full max-w-[1440px] px-6 py-6 xl:px-8">
                    <Page />
                  </div>
                )}
              </ErrorBoundary>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Toaster />
      <ConfirmHost />
    </div>
  );
}
