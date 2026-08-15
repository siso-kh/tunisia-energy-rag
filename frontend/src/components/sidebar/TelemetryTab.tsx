import { Droplets, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import CardShell from "../layout/CardShell";

const DEMAND = [
  { t: "00h", mw: 2100 },
  { t: "04h", mw: 1850 },
  { t: "08h", mw: 2650 },
  { t: "12h", mw: 3400 },
  { t: "16h", mw: 3150 },
  { t: "20h", mw: 4050 },
  { t: "24h", mw: 2450 },
];

const RESERVOIRS = [
  { name: "Sidi Salem", pct: 74 },
  { name: "Gabès Desal.", pct: 91 },
  { name: "Bouhertma", pct: 52 },
];

function GridCard() {
  const { t } = useTranslation();
  return (
    <CardShell title={t("telemetry.gridTitle")} tag={t("telemetry.gridTag")} color="var(--electric)" icon={Zap}>
      <div className="flex items-baseline gap-1.5">
        <span className="text-2xl font-bold tracking-tight text-foreground">4,050</span>
        <span className="text-xs font-semibold text-muted-foreground">{t("telemetry.peak")}</span>
      </div>
      <div className="h-20 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={DEMAND} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
            <defs>
              <linearGradient id="demandFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--electric)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--electric)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="t"
              tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              interval={1}
            />
            <YAxis hide domain={[1500, 4400]} />
            <Tooltip
              cursor={{ stroke: "var(--electric)", strokeWidth: 1, strokeDasharray: "3 3" }}
              contentStyle={{ borderRadius: 12, border: "1px solid var(--border)", fontSize: 12 }}
              labelStyle={{ color: "var(--muted-foreground)", fontSize: 10 }}
              formatter={(v) => [`${Number(v).toLocaleString()} MW`, "Demande"]}
            />
            <Area type="monotone" dataKey="mw" stroke="var(--electric)" strokeWidth={2.5} fill="url(#demandFill)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </CardShell>
  );
}

function WaterCard() {
  const { t } = useTranslation();
  return (
    <CardShell title={t("telemetry.waterTitle")} tag={t("telemetry.waterTag")} color="var(--water)" icon={Droplets}>
      <div className="flex items-baseline gap-1.5">
        <span className="text-2xl font-bold tracking-tight text-foreground">68%</span>
        <span className="text-xs font-semibold text-muted-foreground">{t("telemetry.capacity")}</span>
      </div>
      <div className="space-y-2.5">
        {RESERVOIRS.map((r) => (
          <div key={r.name}>
            <div className="mb-1 flex justify-between text-[11px]">
              <span className="font-medium text-foreground">{r.name}</span>
              <span className="text-muted-foreground">{r.pct}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-water" style={{ width: `${r.pct}%` }} />
            </div>
          </div>
        ))}
      </div>
    </CardShell>
  );
}

export default function TelemetryTab() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <GridCard />
      <WaterCard />
      <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">{t("telemetry.note")}</p>
    </div>
  );
}
