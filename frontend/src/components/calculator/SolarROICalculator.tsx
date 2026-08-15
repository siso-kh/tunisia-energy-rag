import { useMemo, useState } from "react";
import { Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import CardShell from "../layout/CardShell";

// Assumptions for the projection
const PANEL_PRODUCTION_RATIO = 0.6; // monthly kWh produced per kWh consumed (typical PV coverage)

export default function SolarROICalculator() {
  const { t } = useTranslation();

  const [consumption, setConsumption] = useState(350); // kWh / month
  const [panelCost, setPanelCost] = useState(18000); // TND
  const [tariff, setTariff] = useState(0.248); // TND / kWh

  const { monthlySavings, paybackYears, projection } = useMemo(() => {
    const produced = consumption * PANEL_PRODUCTION_RATIO;
    const monthlySavings = produced * tariff;
    const paybackMonths = monthlySavings > 0 ? panelCost / monthlySavings : Infinity;
    const paybackYears = paybackMonths / 12;

    // 20-year cumulative savings projection
    const projection = [];
    let cumulative = -panelCost;
    for (let month = 0; month <= 240; month++) {
      if (month % 12 === 0 || month === 240) {
        projection.push({
          month: Math.round(month / 12),
          savings: Math.round(cumulative),
        });
      }
      cumulative += monthlySavings;
    }
    return { monthlySavings, paybackYears, projection };
  }, [consumption, panelCost, tariff]);

  const sliderClass = "w-full cursor-pointer accent-solar";

  return (
    <div className="flex flex-col gap-4">
      <CardShell title={t("calculator.title")} tag={t("calculator.tag")} color="var(--solar)" icon={Sun}>
        <div>
          <label className="mb-2 flex justify-between text-sm text-foreground">
            <span>{t("calculator.consumption")}</span>
            <span className="font-semibold">{consumption} kWh</span>
          </label>
          <input
            type="range"
            min={50}
            max={2000}
            step={10}
            value={consumption}
            onChange={(e) => setConsumption(Number(e.target.value))}
            className={sliderClass}
          />
        </div>

        <div>
          <label className="mb-2 flex justify-between text-sm text-foreground">
            <span>{t("calculator.panelCost")}</span>
            <span className="font-semibold">{panelCost.toLocaleString()} TND</span>
          </label>
          <input
            type="range"
            min={5000}
            max={80000}
            step={1000}
            value={panelCost}
            onChange={(e) => setPanelCost(Number(e.target.value))}
            className={sliderClass}
          />
        </div>

        <div>
          <label className="mb-2 flex justify-between text-sm text-foreground">
            <span>{t("calculator.tariff")}</span>
            <span className="font-semibold">{tariff.toFixed(3)} TND</span>
          </label>
          <input
            type="range"
            min={0.05}
            max={0.6}
            step={0.005}
            value={tariff}
            onChange={(e) => setTariff(Number(e.target.value))}
            className={sliderClass}
          />
        </div>

        <div className="grid grid-cols-3 gap-3 rounded-lg bg-muted px-3 py-2.5">
          <div>
            <div className="text-[10px] text-muted-foreground">{t("calculator.investment")}</div>
            <div className="text-sm font-bold text-foreground">
              {panelCost.toLocaleString()} TND
            </div>
          </div>
          <div>
            <div className="text-[10px] text-muted-foreground">{t("calculator.monthlySavings")}</div>
            <div className="text-sm font-bold text-solar">
              {Math.round(monthlySavings).toLocaleString()} TND
            </div>
          </div>
          <div>
            <div className="text-[10px] text-muted-foreground">{t("calculator.payback")}</div>
            <div className="text-sm font-bold text-foreground">
              {Number.isFinite(paybackYears)
                ? `${paybackYears.toFixed(1)} ${t("calculator.years")}`
                : "—"}
            </div>
          </div>
        </div>
      </CardShell>

      <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-foreground">
          {t("calculator.savingsChart")}
        </h3>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={projection} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="savingsGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#f5a623" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#f5a623" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
            <XAxis
              dataKey="month"
              tick={{ fontSize: 10 }}
              label={{ value: t("calculator.month"), position: "insideBottom", offset: -2, fontSize: 10 }}
            />
            <YAxis tick={{ fontSize: 10 }} width={60} />
            <Tooltip
              formatter={(value: number) => [`${value.toLocaleString()} TND`, t("calculator.savings")]}
            />
            <Area
              type="monotone"
              dataKey="savings"
              stroke="#f5a623"
              strokeWidth={2}
              fill="url(#savingsGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
