import { lazy, Suspense } from "react";
import { Calculator, Map, Waypoints } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useUIStore, type SidebarTab } from "../../store/uiStore";
import Spinner from "../ui/Spinner";

// Lazy-loaded so Leaflet / Recharts stay out of the initial chat bundle.
const CarteTab = lazy(() => import("../sidebar/CarteTab"));
const TelemetryTab = lazy(() => import("../sidebar/TelemetryTab"));
const CalculatorTab = lazy(() => import("../../components/calculator/SolarROICalculator"));

const TABS: { key: SidebarTab; labelKey: string; icon: typeof Map }[] = [
  { key: "carte", labelKey: "sidebar.carte", icon: Map },
  { key: "telemetrie", labelKey: "sidebar.telemetrie", icon: Waypoints },
  { key: "calculator", labelKey: "sidebar.calculator", icon: Calculator },
];

function PanelFallback() {
  return (
    <div className="flex flex-1 items-center justify-center text-muted-foreground">
      <Spinner className="size-5" />
    </div>
  );
}

export default function EnergySidebar() {
  const { t } = useTranslation();
  const { sidebarTab, setSidebarTab } = useUIStore();

  return (
    <aside className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border bg-card">
      {/* Tab bar */}
      <div className="grid shrink-0 grid-cols-3 gap-1 border-b border-border p-1.5">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const active = sidebarTab === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setSidebarTab(tab.key)}
              className={`flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition-colors ${
                active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="size-3.5" aria-hidden="true" />
              {t(tab.labelKey)}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="chat-scroll min-h-0 flex-1 overflow-y-auto bg-background p-4">
        <Suspense fallback={<PanelFallback />}>
          {sidebarTab === "carte" && <CarteTab />}
          {sidebarTab === "telemetrie" && <TelemetryTab />}
          {sidebarTab === "calculator" && <CalculatorTab />}
        </Suspense>
      </div>
    </aside>
  );
}
